"""Optional LLM narration layer.

Critical design choice: the LLM is OPTIONAL and the whole app works without
it. If no API key is configured, narration degrades gracefully to a
deterministic description built from the node's own metadata (docstring,
type, connections) -- it never crashes, never blocks the core map.

The LLM never decides structure; it only explains a node that the
deterministic parser already identified.
"""

from __future__ import annotations

import os


def llm_available() -> bool:
    """True only if some provider key is configured. Keeps the app fully
    functional offline / in CI without any keys."""
    return any(
        os.getenv(k) and not os.getenv(k, '').startswith('your_')
        for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")
    )


def _fallback_explanation(node: dict, neighbors: list[dict]) -> str:
    """Deterministic, no-LLM explanation from node metadata alone."""
    parts = [f"`{node['qualified_name']}` is a {node['type']}"]
    if node.get("docstring"):
        first_line = node["docstring"].strip().splitlines()[0]
        parts.append(f"documented as: \"{first_line}\"")
    if node.get("is_entrypoint"):
        parts.append("It looks like an entry point into the codebase.")
    if neighbors:
        names = ", ".join(n["name"] for n in neighbors[:5])
        parts.append(f"It connects to: {names}.")
    return " ".join(parts)


def explain_node(
    node: dict,
    neighbors: list[dict],
    model: str = "gpt-4o-mini",
) -> dict:
    """Return {'explanation': str, 'source': 'llm' | 'fallback'}.

    Falls back to a deterministic description when no key is configured or
    the LLM call fails -- callers always get a usable explanation.
    """
    if not llm_available():
        return {"explanation": _fallback_explanation(node, neighbors), "source": "fallback"}

    try:
        from litellm import completion

        neighbor_desc = "\n".join(
            f"- {n['type']} {n['qualified_name']}" for n in neighbors[:10]
        )
        prompt = (
            f"You are helping a new engineer understand a codebase. Explain "
            f"the following {node['type']} in 2-3 sentences, plain language, "
            f"no code.\n\n"
            f"Name: {node['qualified_name']}\n"
            f"Type: {node['type']}\n"
            f"Docstring: {node.get('docstring') or '(none)'}\n"
            f"Connected to:\n{neighbor_desc or '(nothing)'}\n"
        )
        resp = completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=30,
        )
        text = resp.choices[0].message.content or ""
        return {"explanation": text.strip(), "source": "llm"}
    except Exception:
        # Any failure (bad key, network, rate limit) -> graceful fallback.
        return {"explanation": _fallback_explanation(node, neighbors), "source": "fallback"}
