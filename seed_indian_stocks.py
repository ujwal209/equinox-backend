import os
import asyncio
from urllib.parse import urlparse
from dotenv import load_dotenv
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
import itertools
from pydantic import BaseModel, Field
from typing import List

from langchain_groq import ChatGroq

# Load env variables
load_dotenv(".env")

MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or "mongodb://localhost:27017/equinox"
GROQ_API_KEYS = [k.strip() for k in os.getenv("GROQ_API_KEYS", "").split(",") if k.strip()]
TAVILY_API_KEYS = [k.strip() for k in os.getenv("TAVILY_API_KEYS", "").split(",") if k.strip()]

groq_keys_cycle = itertools.cycle(GROQ_API_KEYS) if GROQ_API_KEYS else None
tavily_keys_cycle = itertools.cycle(TAVILY_API_KEYS) if TAVILY_API_KEYS else None

def get_next_groq_key():
    if groq_keys_cycle:
        return next(groq_keys_cycle)
    return None

def get_next_tavily_key():
    if tavily_keys_cycle:
        return next(tavily_keys_cycle)
    return None

# Pydantic Schemas for JSON enforcement
class Sector(BaseModel):
    name: str = Field(description="The formal name of the sector (e.g. Financial Services)")
    slug: str = Field(description="The kebab-case slug of the sector (e.g. financial-services)")
    description: str = Field(description="A short description of what the sector encompasses")

class SectorsResponse(BaseModel):
    sectors: List[Sector] = Field(description="List of 12 major sectors")

class Index(BaseModel):
    symbol: str = Field(description="The Yahoo finance ticker format (e.g. ^NSEI for NIFTY 50, BTC-USD for Bitcoin)")
    name: str = Field(description="The name of the index or cryptocurrency (e.g. NIFTY 50, SENSEX, Bitcoin)")
    exchDisp: str = Field(description="The exchange display name (e.g. NSE, BSE, INDEX, CRYPTO)")
    logo_domain: str = Field(description="The official website domain to fetch the logo from (e.g. nseindia.com)")

class IndicesResponse(BaseModel):
    indices: List[Index] = Field(description="List of indices and cryptos")

class Company(BaseModel):
    symbol: str = Field(description="The real NSE ticker symbol with the .NS suffix (e.g. RELIANCE.NS, TCS.NS)")
    name: str = Field(description="The formal company name (e.g. Reliance Industries Limited)")
    sector: str = Field(description="The exact sector name this company belongs to")

class CompaniesResponse(BaseModel):
    companies: List[Company] = Field(description="List of Indian NSE companies")

async def query_langchain_structured(system_prompt: str, user_prompt: str, response_schema: type[BaseModel]) -> BaseModel:
    """Helper to query Langchain ChatGroq with structured output, cycling through keys on failure."""
    max_retries = max(1, len(GROQ_API_KEYS) * 2)
    
    for i in range(max_retries):
        groq_key = get_next_groq_key()
        if not groq_key:
            return None
            
        try:
            model = ChatGroq(
                model="llama-3.3-70b-versatile",
                api_key=groq_key,
                temperature=0.0,
                max_tokens=4000
            )
            structured_llm = model.with_structured_output(response_schema)
            messages = [
                ("system", system_prompt),
                ("human", user_prompt)
            ]
            response = await structured_llm.ainvoke(messages)
            return response
        except Exception as e:
            print(f"[Warning] llama-3.3-70b-versatile structured query failed with key {groq_key[-4:] if groq_key else ''}: {e}. Trying fallback...")
            try:
                fallback_model = ChatGroq(
                    model="llama-3.1-8b-instant",
                    api_key=groq_key,
                    temperature=0.0,
                    max_tokens=4000
                )
                structured_llm = fallback_model.with_structured_output(response_schema)
                messages = [
                    ("system", system_prompt),
                    ("human", user_prompt)
                ]
                response = await structured_llm.ainvoke(messages)
                return response
            except Exception as ex:
                print(f"[Error] Fallback structured query failed: {ex}. Trying next key if available...")
    return None

async def generate_sectors() -> list:
    print("[LangChain] Generating Market Sectors dynamically...")
    system_prompt = "You are an expert financial data compiler."
    user_prompt = "Generate exactly 12 major Indian stock market sectors."
    
    data = await query_langchain_structured(system_prompt, user_prompt, SectorsResponse)
    sectors = data.sectors if data else []
    
    if sectors:
        print(f" -> Generated {len(sectors)} sectors.")
    else:
        print(" -> Failed to generate sectors.")
    return [s.model_dump() for s in sectors]

async def generate_indices() -> list:
    print("[LangChain] Generating Market Indices dynamically...")
    system_prompt = "You are an expert financial data compiler."
    user_prompt = "Generate 5 top Indian stock market indices and popular cryptos."
    
    data = await query_langchain_structured(system_prompt, user_prompt, IndicesResponse)
    indices = data.indices if data else []
    
    indices_list = [i.model_dump() for i in indices]
    # Process logos for indices
    for idx in indices_list:
        domain = idx.get("logo_domain", "")
        if domain:
            idx["logo"] = f"https://www.google.com/s2/favicons?sz=128&domain={domain}"
        else:
            idx["logo"] = ""
            
    if indices_list:
        print(f" -> Generated {len(indices_list)} indices.")
    else:
        print(" -> Failed to generate indices.")
    return indices_list

async def generate_companies_for_sector(sector_name: str, target_count: int = 100) -> list:
    print(f"\n[LangChain] Generating Companies for sector: {sector_name} (Target: {target_count})")
    all_companies = []
    seen_symbols = set()
    
    chunk_size = 25
    max_empty_responses = 2
    empty_responses = 0
    
    while len(all_companies) < target_count:
        needed = min(chunk_size, target_count - len(all_companies))
        
        exclude_list = ", ".join(list(seen_symbols)) if seen_symbols else "None"
        system_prompt = "You are an expert financial data compiler. Generate Indian stock listings on the National Stock Exchange (NSE)."
        user_prompt = (
            f"You MUST generate exactly {needed} unique, real, active company stock listings for the sector: {sector_name}.\n\n"
            "Rules:\n"
            "- The symbols MUST be real NSE ticker symbols and MUST end with the suffix \".NS\" (e.g. \"RELIANCE.NS\").\n"
            "- Do not invent symbols. Only provide real listed Indian companies.\n"
            f"- DO NOT include any of these symbols (they are already generated): {exclude_list}"
        )
        
        data = await query_langchain_structured(system_prompt, user_prompt, CompaniesResponse)
        new_companies = data.companies if data else []
        
        if not new_companies:
            print(f"[Warning] Empty response received for {sector_name}.")
            empty_responses += 1
            if empty_responses >= max_empty_responses:
                print(f" -> Stopping early for {sector_name} due to consecutive empty responses.")
                break
            continue
            
        added = 0
        for comp_model in new_companies:
            comp = comp_model.model_dump()
            sym = comp.get("symbol", "").upper().strip()
            if sym and sym not in seen_symbols:
                seen_symbols.add(sym)
                comp["sector"] = sector_name
                all_companies.append(comp)
                added += 1
                
        print(f" -> Generated {added} new unique companies. Total so far: {len(all_companies)}/{target_count}")
        
        if added == 0:
            empty_responses += 1
            if empty_responses >= max_empty_responses:
                print(f" -> Stopping early for {sector_name} due to repeated duplicate/empty responses.")
                break
        else:
            empty_responses = 0
            
        await asyncio.sleep(0.5)
        
    return all_companies

async def fetch_logo_domain(client: httpx.AsyncClient, name: str) -> str:
    tavily_key = get_next_tavily_key()
    if not tavily_key:
        return ""
    try:
        res = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": tavily_key,
                "query": f"{name} official corporate website url",
                "search_depth": "basic",
                "max_results": 1
            }
        )
        if res.status_code == 200:
            data = res.json()
            results = data.get("results", [])
            if results:
                url = results[0].get("url", "")
                parsed = urlparse(url)
                domain = parsed.netloc
                if domain.startswith("www."):
                    domain = domain[4:]
                return domain
        elif res.status_code == 429:
            print(f"[Warning] Rate limited on Tavily for key {tavily_key[-4:]}")
    except Exception as e:
        print(f"[Warning] Tavily domain lookup failed for {name}: {e}")
    return ""

async def main():
    print("[Equinox Seeder] Initializing scalable LangChain-powered database seed process for 1000+ companies...")
    
    # 1. Dynamically generate Indices and Sectors
    sectors_data = await generate_sectors()
    indices_data = await generate_indices()
    
    if not sectors_data:
        print("[Error] Could not generate sectors. Exiting.")
        return
        
    # 2. Query companies from Groq per sector in chunks
    print("\n[Step 2] Querying LLM to compile unique top Indian stocks dynamically (Part by Part)...")
    companies_data = []
    
    # Let's aim for ~1000 companies total. If there are e.g. 12 sectors, we need ~85 per sector.
    target_per_sector = max(30, 1000 // len(sectors_data) + 5)
    
    for sector in sectors_data:
        sector_companies = await generate_companies_for_sector(sector["name"], target_per_sector)
        if sector_companies:
            companies_data.extend(sector_companies)
            
    if not companies_data:
        print("[Error] No companies data generated. Exiting.")
        return
        
    print(f"\n -> Generated {len(companies_data)} unique companies across {len(sectors_data)} sectors.")
    
    # 3. Connect to database
    db_name = MONGO_URI.split("/")[-1].split("?")[0] or "equinox"
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[db_name]
    
    indices_col = db["indices"]
    sectors_col = db["sectors"]
    companies_col = db["companies"]
    
    # Clear collections
    print("\n -> Clearing 'indices', 'sectors', and 'companies' collections...")
    await indices_col.delete_many({})
    await sectors_col.delete_many({})
    await companies_col.delete_many({})
    
    # 4. Seed Indices
    print("\n -> Seeding dynamic indices...")
    if indices_data:
        await indices_col.insert_many(indices_data)
        print(f" -> Successfully seeded {len(indices_data)} indices.")
    
    # 5. Seed Sectors
    print("\n -> Seeding dynamic sectors...")
    await sectors_col.insert_many(sectors_data)
    print(f" -> Successfully seeded {len(sectors_data)} sectors.")
    
    # Fetch inserted sectors to map sector name -> sector_id
    db_sectors = await sectors_col.find({}).to_list(length=100)
    sector_map = {s["name"]: s["_id"] for s in db_sectors}
    
    # 6. Search and Compile Company Website Logo domains
    print(f"\n -> Resolving corporate logo domains for {len(companies_data)} companies using Tavily...")
    
    final_companies = []
    seen_symbols = set()
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        chunk_size = 10
        for i in range(0, len(companies_data), chunk_size):
            chunk = companies_data[i:i+chunk_size]
            tasks = [fetch_logo_domain(client, item["name"]) for item in chunk]
            domains = await asyncio.gather(*tasks)
            
            for idx, item in enumerate(chunk):
                sym = item["symbol"].upper().strip()
                if sym in seen_symbols:
                    continue
                seen_symbols.add(sym)
                
                domain = domains[idx]
                logo_url = ""
                if domain:
                    logo_url = f"https://www.google.com/s2/favicons?sz=128&domain={domain}"
                
                sector_name = item.get("sector", sectors_data[0]["name"])
                # Map exactly to what we generated
                sector_id = sector_map.get(sector_name)
                
                final_companies.append({
                    "symbol": sym,
                    "name": item["name"],
                    "sector_id": sector_id,
                    "sector_name": sector_name,
                    "logo": logo_url,
                    "exchDisp": "NSE",
                    "typeDisp": "Equity"
                })
            
            print(f" -> Processed Tavily domains for {min(i + chunk_size, len(companies_data))} / {len(companies_data)} companies...")
            await asyncio.sleep(0.5)
            
    # 7. Insert Companies
    print(f"\n -> Inserting {len(final_companies)} dynamic companies into MongoDB...")
    if final_companies:
        await companies_col.insert_many(final_companies)
    print(f"[Success] Fully dynamic scale seeding process complete! Seeded {len(final_companies)} companies.")

if __name__ == "__main__":
    asyncio.run(main())
