"""Client for LM Studio's local OpenAI-compatible server (port of LMStudioService.swift).

Blocking calls built on ``urllib``; the UI runs them on a worker thread.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_BASE_URL = "http://127.0.0.1:1234"


@dataclass(frozen=True)
class LMModel:
    """A model as reported by LM Studio's local server."""
    id: str
    type: str = "unknown"    # llm / vlm / embeddings / unknown
    state: str = "unknown"   # loaded / not-loaded / unknown

    @property
    def is_loaded(self) -> bool:
        return self.state == "loaded"

    @property
    def is_chat_model(self) -> bool:
        return self.type != "embeddings" and "embed" not in self.id.lower()


@dataclass
class CleanupRequest:
    paragraph: str
    max_words: int
    instructions: str
    model: str
    temperature: float = 0.2


@dataclass(frozen=True)
class CleanupResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LMStudioError(Exception):
    """Base class; ``str(err)`` is the user-facing message."""


class Unreachable(LMStudioError):
    def __init__(self, url: str, why: str):
        super().__init__(
            f"LM Studio is not reachable at {url} ({why}). "
            "In LM Studio open the Developer tab and start the local server.")


class BadStatus(LMStudioError):
    def __init__(self, code: int, message: str):
        super().__init__(f"LM Studio returned HTTP {code}: {message}")
        self.code = code


class EmptyResponse(LMStudioError):
    def __init__(self) -> None:
        super().__init__("LM Studio returned an empty response.")


class ExhaustedThinking(LMStudioError):
    def __init__(self) -> None:
        super().__init__(
            "The model spent its whole output budget on reasoning and produced no text. "
            "Pick a non-thinking model or a smaller paragraph, or lower the model's "
            "reasoning effort in LM Studio.")


class InvalidJSON(LMStudioError):
    def __init__(self) -> None:
        super().__init__("Could not parse the LM Studio response.")


class NoModel(LMStudioError):
    def __init__(self) -> None:
        super().__init__("No chat model is available in LM Studio. "
                         "Load one in LM Studio or pick one in Settings (Ctrl+,).")


SYSTEM_PROMPT = """\
You are a proofreader, not a rewriter. The author wrote this LaTeX paragraph in their own words and wants it to stay in their own words.

Your job:
- Fix spelling, grammar, punctuation, capitalization and LaTeX syntax.
- Keep the author's wording, sentence order and voice. Do not paraphrase, do not replace words with synonyms, do not merge, split, add or remove sentences, do not "improve" style.
- You have a small allowance of word changes for places where a sentence is ungrammatical or unreadable as written. The allowance is stated in the request; stay well inside it. Any change beyond the allowance will be discarded automatically, so spend it only where it matters.
- Preserve every LaTeX command, macro, math expression, citation, reference, label and environment exactly, unless it is syntactically broken.
- Keep the line breaks of the input.

Reply with the corrected paragraph and nothing else: no explanations, no code fences, no quotation marks, no <paragraph> tags. If nothing needs fixing, return the paragraph unchanged."""


def user_prompt(req: CleanupRequest) -> str:
    if req.max_words == 0:
        p = ("Proofread this paragraph. Fix only spelling, punctuation, capitalization "
             "and LaTeX syntax. Do not add, delete or replace any word.")
    else:
        p = (f"Proofread this paragraph. Fix spelling, punctuation, capitalization and "
             f"LaTeX syntax freely. Beyond that you may add, delete or replace at most "
             f"{req.max_words} words in total, only to repair grammar or an unreadable "
             f"phrase — never to reword what is already correct. Keep everything else "
             f"exactly as the author wrote it.")
    extra = (req.instructions or "").strip()
    if extra:
        p += ("\n\nAdditional instructions from the author (still within the word "
              f"allowance): {extra}")
    p += (f"\n\nParagraph:\n<paragraph>\n{req.paragraph}\n</paragraph>\n\n"
          "Reply with only the corrected paragraph.")
    return p


_THINK_RE = re.compile(r"<think>[\s\S]*?</think>")


def extract_paragraph(raw: str) -> str:
    """Strip reasoning blocks, fences, wrapper tags and chatty lead-ins that local
    models tend to add despite instructions."""
    s = _THINK_RE.sub("", raw).strip()
    if s.startswith("```"):
        nl = s.find("\n")
        s = s[nl + 1:] if nl >= 0 else ""
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    if s.startswith("<paragraph>"):
        s = s[len("<paragraph>"):]
    if s.endswith("</paragraph>"):
        s = s[:-len("</paragraph>")]
    s = s.strip()
    # "Here is the revised paragraph:" followed by a blank line.
    parts = s.split("\n\n")
    if len(parts) >= 2:
        first = parts[0]
        if len(first) < 90 and first.endswith(":") and "\\" not in first:
            s = "\n\n".join(parts[1:]).strip()
    return s


def pick_model(models: list[LMModel]) -> str | None:
    """Picks a model when none is configured: a loaded chat model first, then any."""
    chat = [m for m in models if m.is_chat_model]
    if not chat:
        return None
    for m in chat:
        if m.is_loaded:
            return m.id
    return chat[0].id


def parse_response(obj: dict) -> CleanupResult:
    choices = obj.get("choices") or []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
    message = message if isinstance(message, dict) else {}
    content = message.get("content")
    if isinstance(content, list):
        text = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    elif isinstance(content, str):
        text = content
    else:
        text = ""
    cleaned = extract_paragraph(text)
    if not cleaned:
        # Reasoning models can burn the whole budget on thinking; say so.
        if message.get("reasoning_content"):
            raise ExhaustedThinking()
        raise EmptyResponse()
    usage = obj.get("usage") or {}
    return CleanupResult(
        cleaned,
        obj.get("model") or "",
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
    )


class LMStudioService:
    def __init__(self, base_url: str):
        s = (base_url or "").strip()
        if not s:
            s = DEFAULT_BASE_URL
        if "://" not in s:
            s = "http://" + s
        while s.endswith("/"):
            s = s[:-1]
        if s.endswith("/v1"):
            s = s[:-3]
        self.base_url = s or DEFAULT_BASE_URL

    # -- HTTP --------------------------------------------------------------

    def _send(self, path: str, body: dict | None, timeout: float) -> dict:
        url = f"{self.base_url}/{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url, data=data, method="POST" if data else "GET",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            status = exc.code
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise Unreachable(self.base_url, str(getattr(exc, "reason", exc))) from exc

        try:
            obj = json.loads(payload.decode("utf-8", "replace"))
        except ValueError:
            obj = None
        if status != 200:
            msg = payload[:600].decode("utf-8", "replace")
            if isinstance(obj, dict) and "error" in obj:
                err = obj["error"]
                if isinstance(err, dict) and isinstance(err.get("message"), str):
                    msg = err["message"]
                elif isinstance(err, str):
                    msg = err
            raise BadStatus(status, msg)
        if not isinstance(obj, dict):
            raise InvalidJSON()
        return obj

    def list_models(self) -> list[LMModel]:
        """Prefers ``/api/v0/models`` (has type and load state); falls back to ``/v1/models``."""
        try:
            v0 = self._send("api/v0/models", None, 10)
        except LMStudioError:
            v0 = None
        if isinstance(v0, dict) and isinstance(v0.get("data"), list):
            out = []
            for d in v0["data"]:
                if isinstance(d, dict) and isinstance(d.get("id"), str):
                    out.append(LMModel(d["id"], d.get("type") or "unknown",
                                       d.get("state") or "unknown"))
            return out
        v1 = self._send("v1/models", None, 10)
        return [LMModel(d["id"]) for d in (v1.get("data") or [])
                if isinstance(d, dict) and isinstance(d.get("id"), str)]

    def send_chat(self, body: dict) -> dict:
        return self._send("v1/chat/completions", body, 600)

    def clean_up(self, req: CleanupRequest) -> CleanupResult:
        if not req.model:
            raise NoModel()
        body = {
            "model": req.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt(req)},
            ],
            "temperature": req.temperature,
            "max_tokens": 8192,
            "stream": False,
        }
        return parse_response(self.send_chat(body))
