import json
from typing import TypedDict, Annotated, List, Dict, Any
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_groq import ChatGroq
from langchain_core.tools import tool
from config.keys import groq_keys, tavily_keys
from tavily import TavilyClient

# Define State
class State(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    model_name: str

# Define Tools
@tool
def web_search(query: str) -> str:
    """Search the web for real-time market sentiment, news, or similar stocks."""
    tavily_api_key = tavily_keys.get_next_key()
    try:
        client = TavilyClient(api_key=tavily_api_key)
        response = client.search(query=query, search_depth="advanced", max_results=5)
        # Return a JSON string so the LLM can easily parse the results and we can extract sources later
        results = []
        for res in response.get("results", []):
            results.append({
                "title": res.get("title"),
                "url": res.get("url"),
                "content": res.get("content")
            })
        return json.dumps(results)
    except Exception as e:
        return json.dumps({"error": str(e)})

tools = [web_search]

# Define Nodes
def chatbot(state: State):
    requested_model = state.get("model_name", "Llama 3 70B")
    
    # Map requested models to Groq models
    actual_model = "llama-3.3-70b-versatile"
    if "GPT" in requested_model:
        actual_model = "llama-3.1-8b-instant"
        
    models_to_try = [
        actual_model,
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile"
    ]
    
    # Try multiple keys and models to bypass rate limits
    num_keys = max(1, len(groq_keys.keys)) if hasattr(groq_keys, 'keys') else 1
    
    for _ in range(min(3, num_keys)):
        api_key = groq_keys.get_next_key()
        for model in models_to_try:
            try:
                llm = ChatGroq(
                    api_key=api_key,
                    model=model,
                    temperature=0.3,
                    max_tokens=2048
                )
                llm_with_tools = llm.bind_tools(tools)
                
                try:
                    response = llm_with_tools.invoke(state["messages"])
                except Exception as e:
                    if "tool_use_failed" in str(e).lower() or "400" in str(e):
                        # Fallback invocation without tools
                        response = llm.invoke(state["messages"])
                    else:
                        raise e
                return {"messages": [response]}
            except Exception as e:
                # If rate limited, log and try next fallback
                import logging
                logging.getLogger("uvicorn.error").warning(f"Groq request for model {model} failed: {e}. Trying fallback...")
                continue
                
    # If all options fail, raise the last exception
    raise RuntimeError("All configured AI models and keys are currently rate-limited. Please try again shortly.")

class BasicToolNode:
    """A node that runs the tools requested in the last AIMessage."""
    def __init__(self, tools: list) -> None:
        self.tools_by_name = {tool.name: tool for tool in tools}

    def __call__(self, inputs: dict):
        if messages := inputs.get("messages", []):
            message = messages[-1]
        else:
            raise ValueError("No message found in input")
        
        outputs = []
        for tool_call in message.tool_calls:
            tool_result = self.tools_by_name[tool_call["name"]].invoke(
                tool_call["args"]
            )
            outputs.append(
                ToolMessage(
                    content=json.dumps(tool_result) if not isinstance(tool_result, str) else tool_result,
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                )
            )
        return {"messages": outputs}

tool_node = BasicToolNode(tools=[web_search])

def route_tools(state: State):
    if isinstance(state, list):
        ai_message = state[-1]
    elif messages := state.get("messages", []):
        ai_message = messages[-1]
    else:
        raise ValueError(f"No messages found in input state to tool_edge: {state}")
    
    if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
        return "tools"
    return END

# Build Graph
graph_builder = StateGraph(State)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_node("tools", tool_node)
graph_builder.add_conditional_edges("chatbot", route_tools, {"tools": "tools", END: END})
graph_builder.add_edge("tools", "chatbot")
graph_builder.add_edge(START, "chatbot")
graph = graph_builder.compile()

def invoke_agent(history: List[Dict[str, str]], context_symbol: str = None, context_data: dict = None, model_name: str = "Llama 3 70B") -> tuple[str, List[Dict[str, Any]]]:
    """
    Invokes the LangGraph agent with the provided history.
    Extracts and returns the final AIMessage content and a list of unique sources (URLs).
    """
    # Build initial state messages
    system_prompt = (
        "You are Equinox AI, an advanced intelligent trading assistant.\n"
        "You analyze market trends, evaluate portfolio risks, and provide algorithmic insights.\n"
        "ALWAYS use the web_search tool to look up real-time sentiment, news, and similar stocks before making a definitive recommendation.\n"
        "Provide EXACT actions (Buy/Sell/Hold) with clear rationale based on your search.\n"
        "If a stock is discussed, identify correlated or similar stocks.\n"
        "Format your response beautifully using Markdown.\n"
    )
    if context_symbol:
        system_prompt += f"\\nNOTE: The user is currently viewing the stock {context_symbol}. If they ask a general question, assume it is about {context_symbol}."
    if context_data and "marketData" in context_data and context_data["marketData"]:
        price = context_data["marketData"].get("price")
        change = context_data["marketData"].get("change")
        system_prompt += f"\\nCurrent Market Data for {context_symbol}: Price is {price}, Daily Change is {change}."
    system_prompt += "\\n"

    messages = [SystemMessage(content=system_prompt)]
    
    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "ai":
            messages.append(AIMessage(content=msg["content"]))

    # Invoke graph with friendly error handling
    try:
        final_state = graph.invoke({"messages": messages, "model_name": model_name})
    except Exception as e:
        import traceback
        traceback.print_exc()
        error_str = str(e).lower()
        if "rate_limit_exceeded" in error_str or "413" in error_str or "429" in error_str:
            return "I am currently receiving too many requests or processing too much data. Please switch to a faster model or try again in a few moments.", []
        return f"I encountered an internal error while analyzing the data. Please try again. [Internal Details: {str(e)}]", []
        
    # Extract final AI response
    final_message = final_state["messages"][-1]
    content = final_message.content

    # Extract sources from all ToolMessages in the execution path
    sources_dict = {}
    for msg in final_state["messages"]:
        if isinstance(msg, ToolMessage) and msg.name == "web_search":
            try:
                results = json.loads(msg.content)
                if isinstance(results, list):
                    for res in results:
                        if "url" in res:
                            sources_dict[res["url"]] = {
                                "title": res.get("title", "Source"),
                                "url": res["url"],
                                "content": res.get("content", "")
                            }
            except Exception:
                pass
                
    sources = list(sources_dict.values())
    
    return content, sources
