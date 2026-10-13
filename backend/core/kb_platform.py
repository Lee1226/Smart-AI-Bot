"""Platform scope for knowledge, based on document metadata, path and title.

Business docs may mention multiple clients in their body; those mentions must
not turn shared rules into platform-specific implementation instructions.
"""
import re

_ALIASES = {
    "android": "android", "安卓": "android",
    "ios": "ios", "iphone": "ios",
    "harmony": "harmony", "harmonyos": "harmony", "鸿蒙": "harmony",
    "server": "server", "web": "web", "desktop": "desktop",
}


def normalize_platform(value: str) -> str:
    return _ALIASES.get(str(value).strip().lower(), str(value).strip().lower())


def document_platforms(path: str = "", title: str = "", content: str = "") -> set:
    # Structured metadata and directory scope take precedence over a title.
    metadata_header = re.split(r"^#\s+", content, maxsplit=1, flags=re.M)[0]
    metadata = re.search(r"^(?:platforms?|平台)\s*:\s*([^\n]+)", metadata_header, re.M | re.I)
    if metadata:
        return {normalize_platform(p.strip(" []'\""))
                for p in re.split(r"[,，|\s]+", metadata.group(1)) if p.strip(" []'\"")}
    match = re.search(r"(?:^|/)(?:platforms|incidents)/([^/]+)(?:/|$)", str(path).replace("\\", "/"), re.I)
    if match:
        platform = normalize_platform(match.group(1))
        # Shared rendering/framework docs remain usable on their host clients.
        return set() if platform in {"flutter", "kmp", "cocos", "shared", "common"} else {platform}
    heading = title or next(iter(re.findall(r"^#\s+(.+)$", content, re.M)), "")
    names = re.findall(r"\b(?:android|ios|iphone|harmonyos|harmony)\b|安卓|鸿蒙", heading, re.I)
    return {normalize_platform(name) for name in names}


def allows_document(platform: str, path: str = "", title: str = "", content: str = "") -> bool:
    scoped = document_platforms(path, title, content)
    return not platform or not scoped or normalize_platform(platform) in scoped or bool(scoped & {"all", "shared", "common", "通用"})


def filter_cli_knowledge(text: str, platform: str) -> tuple:
    """Filter CLI path-delimited fragments before applying the text budget."""
    blocks = re.split(r"(?=^###\s+\S+\.md\s*$)", text, flags=re.M)
    has_paths = len(blocks) > 1
    kept, sources, excluded = [], [], 0
    for block in blocks:
        if has_paths and not re.match(r"^###\s+", block):
            continue  # CLI's search header is not a knowledge hit.
        path_match = re.match(r"^###\s+(\S+\.md)\s*$", block, re.M)
        path = path_match.group(1) if path_match else ""
        if not allows_document(platform, path=path, content=block):
            excluded += 1
            continue
        if block.strip():
            kept.append(block.strip())
            if path:
                sources.append(path)
    return "\n\n---\n\n".join(kept), sources, excluded
