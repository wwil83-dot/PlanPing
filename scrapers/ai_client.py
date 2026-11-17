"""
ai_client.py - one tiny wrapper so the scripts can use Claude OR Groq.

Env:
  AI_PROVIDER   "anthropic" (default) or "groq"
  AI_MODEL      model name. Default for anthropic is set by the caller; REQUIRED for groq
                (copy a current model name from console.groq.com, e.g. a Llama or gpt-oss model).
  ANTHROPIC_API_KEY / GROQ_API_KEY
"""
import os, sys

PROVIDER = os.environ.get("AI_PROVIDER", "anthropic").lower()


def model_name(default_anthropic: str) -> str:
    m = os.environ.get("AI_MODEL")
    if PROVIDER == "groq":
        if not m:
            sys.exit("AI_PROVIDER=groq needs AI_MODEL set (copy a model name from console.groq.com).")
        return m
    return m or default_anthropic


class Client:
    def __init__(self):
        if PROVIDER == "groq":
            key = os.environ.get("GROQ_API_KEY")
            if not key:
                sys.exit("GROQ_API_KEY is not set (add it as a GitHub secret).")
            from openai import AsyncOpenAI
            self._c = AsyncOpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
        else:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                sys.exit("ANTHROPIC_API_KEY is not set (add it as a GitHub secret).")
            from anthropic import AsyncAnthropic
            self._c = AsyncAnthropic()

    async def complete(self, model: str, system: str, user: str, max_tokens: int = 300) -> str:
        if PROVIDER == "groq":
            r = await self._c.chat.completions.create(
                model=model, max_tokens=max_tokens, temperature=0.2,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            return r.choices[0].message.content or ""
        m = await self._c.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in m.content if b.type == "text")
