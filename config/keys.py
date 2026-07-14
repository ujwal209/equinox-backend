import os
import itertools

class KeyManager:
    def __init__(self, env_var_name: str):
        keys_str = os.getenv(env_var_name, "")
        if keys_str:
            self.keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        else:
            self.keys = []
            
        if not self.keys:
            # Fallback to single key if comma separated list not provided
            single_key = os.getenv(env_var_name.replace("KEYS", "KEY")) or os.getenv(env_var_name[:-1])
            if single_key:
                self.keys = [single_key]
                
        self._iterator = itertools.cycle(self.keys) if self.keys else None

    def get_next_key(self) -> str:
        if not self._iterator:
            raise ValueError(f"No API keys configured for. Please set environment variables.")
        return next(self._iterator)

groq_keys = KeyManager("GROQ_API_KEYS")
tavily_keys = KeyManager("TAVILY_API_KEYS")
