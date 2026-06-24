"""
MODEL ROUTER — Provider-Agnostic AI Call Wrapper
==================================================
Routes AI calls to the right model based on:
  - Classification result (dynamic/static/complex)
  - model_config.py settings
  - Automatic fallback if primary model unavailable

Supports: OpenAI, Anthropic (add more providers easily)

Usage:
    router = ModelRouter(openai_api_key="...", anthropic_api_key="...")
    response = router.call("simple", system_prompt, user_message)
"""

import json
import os
import requests
from typing import Dict, Optional
from openai import OpenAI
from bot.core.model_config import MODEL_CONFIG


class ModelRouter:
    """
    Provider-agnostic AI model caller.
    Routes to correct provider/model based on config.
    """

    def __init__(self, openai_api_key: str = "", anthropic_api_key: str = ""):
        self.openai_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.anthropic_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")

        self.openai_client = OpenAI(api_key=self.openai_key) if self.openai_key else None

        self._stats = {'calls': 0, 'errors': 0, 'by_model': {}}

        providers = []
        if self.openai_key:
            providers.append("OpenAI")
        if self.anthropic_key:
            providers.append("Anthropic")
        print(f"✅ ModelRouter initialized (providers: {', '.join(providers) or 'none'})")

    # ══════════════════════════════════════════════════════════════════════
    #  MAIN: Call the right model
    # ══════════════════════════════════════════════════════════════════════

    def call(
        self,
        role: str,
        system_prompt: str,
        user_message: str,
        override_config: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        Call the configured model for this role.

        Args:
            role: "classifier", "simple", "complex", "drive_analysis"
            system_prompt: system prompt string
            user_message: user message string
            override_config: optional dict to override model_config for this call

        Returns:
            Response text string, or None on error
        """
        config = override_config or MODEL_CONFIG.get(role, MODEL_CONFIG.get("simple"))
        provider = config.get("provider", "openai")
        model = config.get("model", "gpt-4o-mini")
        fallback = config.get("fallback_model", "gpt-4o-mini")
        max_tokens = config.get("max_tokens", 400)
        temperature = config.get("temperature", 0.3)

        self._stats['calls'] += 1

        # Try primary model, then fallback
        for attempt_model in [model, fallback]:
            try:
                if provider == "openai":
                    result = self._call_openai(
                        attempt_model, system_prompt, user_message,
                        max_tokens, temperature,
                    )
                elif provider == "anthropic":
                    result = self._call_anthropic(
                        attempt_model, system_prompt, user_message,
                        max_tokens, temperature,
                    )
                else:
                    print(f"   ⚠️ Unknown provider: {provider}")
                    return None

                if result:
                    # Track usage
                    self._stats['by_model'][attempt_model] = \
                        self._stats['by_model'].get(attempt_model, 0) + 1
                    return result

            except Exception as e:
                error_msg = str(e)
                if 'model_not_found' in error_msg or '404' in error_msg:
                    print(f"   ⚠️ Model {attempt_model} not available, trying fallback...")
                    continue
                print(f"   ⚠️ ModelRouter error ({attempt_model}): {e}")
                if attempt_model == fallback:
                    self._stats['errors'] += 1
                    return None
                continue

        self._stats['errors'] += 1
        return None

    # ══════════════════════════════════════════════════════════════════════
    #  PROVIDERS
    # ══════════════════════════════════════════════════════════════════════

    def _call_openai(
        self, model: str, system: str, user: str,
        max_tokens: int, temperature: float,
    ) -> Optional[str]:
        """Call OpenAI API."""
        if not self.openai_client:
            print("   ⚠️ OpenAI not configured")
            return None

        resp = self.openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content.strip()

    def _call_anthropic(
        self, model: str, system: str, user: str,
        max_tokens: int, temperature: float,
    ) -> Optional[str]:
        """Call Anthropic Claude API."""
        if not self.anthropic_key:
            print("   ⚠️ Anthropic not configured")
            return None

        headers = {
            "x-api-key": self.anthropic_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }

        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers, json=payload, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block["text"]
        return text.strip() if text else None

    # ══════════════════════════════════════════════════════════════════════
    #  STATS
    # ══════════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        """Return router statistics for daily report."""
        return self._stats.copy()

    def reset_stats(self):
        """Reset stats."""
        self._stats = {'calls': 0, 'errors': 0, 'by_model': {}}
