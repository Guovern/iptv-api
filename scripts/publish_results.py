import argparse
import html
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAX_RELEASE_ASSET_BYTES = 2 * 1024 * 1024 * 1024 - 1
MAX_PAGES_SITE_BYTES = 1024 * 1024 * 1024

OPTIONAL_ASSETS = {
    "ipv4/result.txt": "ipv4.txt",
    "ipv4/result.m3u": "ipv4.m3u",
    "ipv6/result.txt": "ipv6.txt",
    "ipv6/result.m3u": "ipv6.m3u",
    "epg/epg.xml": "epg.xml",
    "epg/epg.gz": "epg.gz",
}

LOG_ASSETS = {
    "log/log.log": "log.log",
    "log/runtime.jsonl": "runtime.jsonl",
    "log/result.log": "result.log",
    "log/result.jsonl": "result.jsonl",
    "log/speed_test.log": "speed_test.log",
    "log/speed_test.jsonl": "speed_test.jsonl",
    "log/statistic.log": "statistic.log",
    "log/statistic.jsonl": "statistic.jsonl",
    "log/unmatch.log": "unmatch.log",
    "log/unmatch.jsonl": "unmatch.jsonl",
}
PAGE_LOG_ASSETS = {"result.log", "unmatch.log"}

PAGE_ASSET_GROUPS = (
    {
        "id": "all-results",
        "eyebrow": "结果 / Results",
        "title": "完整结果 / All networks",
        "description": "包含所有可用频道。 / Includes all available channels.",
        "assets": (
            ("result.m3u", "M3U", "完整播放列表", "Complete playlist"),
            ("result.txt", "TXT", "完整文本列表", "Complete text list"),
        ),
    },
    {
        "id": "ipv4-results",
        "eyebrow": "IPv4",
        "title": "IPv4 结果 / IPv4 only",
        "description": "仅包含 IPv4 地址，适用于未启用 IPv6 或 IPv6 连接不稳定的网络。 / Contains IPv4 addresses only, for networks without IPv6 or with unstable IPv6.",
        "assets": (
            ("ipv4.m3u", "M3U", "IPv4 播放列表", "IPv4 playlist"),
            ("ipv4.txt", "TXT", "IPv4 文本列表", "IPv4 text list"),
        ),
    },
    {
        "id": "ipv6-results",
        "eyebrow": "IPv6",
        "title": "IPv6 结果 / IPv6 only",
        "description": "仅包含 IPv6 地址，请在当前网络和播放设备支持 IPv6 时使用。 / Contains IPv6 addresses only; use it when the current network and player support IPv6.",
        "assets": (
            ("ipv6.m3u", "M3U", "IPv6 播放列表", "IPv6 playlist"),
            ("ipv6.txt", "TXT", "IPv6 文本列表", "IPv6 text list"),
        ),
    },
    {
        "id": "epg-results",
        "eyebrow": "EPG",
        "title": "节目单 / Programme guide",
        "description": "为支持 EPG 的播放器提供节目单数据，GZIP 格式体积更小。 / Programme guide data for EPG players; GZIP uses less space.",
        "assets": (
            ("epg.gz", "GZIP", "EPG 压缩文件", "Compressed EPG"),
            ("epg.xml", "XML", "EPG XML 文件", "EPG XML data"),
        ),
    },
    {
        "id": "run-logs",
        "eyebrow": "日志 / Logs",
        "title": "结果记录 / Result records",
        "description": "查看结果与未匹配频道记录；完整运行日志可在工作流和 Release 中查看。 / View result and unmatched channel records; full run logs are available in the workflow and Release.",
        "assets": (
            ("result.log", "LOG", "结果日志", "Result log"),
            ("unmatch.log", "LOG", "未匹配频道", "Unmatched channels"),
        ),
    },
)

REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RELEASE_TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
DEFAULT_PAGE_FAVICON = Path(__file__).resolve().parent.parent / "static/images/favicon.svg"
PREVIEWABLE_PAGE_ASSETS = {
    "result.m3u",
    "result.txt",
    "ipv4.m3u",
    "ipv4.txt",
    "ipv6.m3u",
    "ipv6.txt",
    "epg.xml",
    "epg.gz",
    *PAGE_LOG_ASSETS,
}


def _normalize_http_url(value, label):
    url = str(value or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{label} must be an absolute HTTP(S) URL: {value}")
    return url


def _resolve_output_file(path, output_dir, allow_empty=False):
    candidate = path if path.is_absolute() else Path.cwd() / path
    output_root = output_dir.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(output_root)
    except ValueError as exc:
        raise ValueError(f"Release input must be inside {output_root}: {path}") from exc
    if candidate.is_symlink() or not resolved.is_file():
        raise ValueError(f"Release input must be a regular file: {path}")
    size = resolved.stat().st_size
    if size <= 0 and not allow_empty:
        raise ValueError(f"Release input is empty: {path}")
    if size > MAX_RELEASE_ASSET_BYTES:
        raise ValueError(f"Release input exceeds the GitHub release asset limit: {path}")
    return resolved


def build_release_metadata(time_zone_name, now=None):
    try:
        configured_zone = ZoneInfo(time_zone_name)
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"Invalid release time zone: {time_zone_name}") from exc
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local_time = instant.astimezone(configured_zone).replace(microsecond=0)
    offset = local_time.strftime("%z")
    offset_direction = "plus" if offset.startswith("+") else "minus"
    offset_token = f"utc-{offset_direction}-{offset[1:]}"
    return {
        "tag": f"playlist-{local_time:%Y%m%d-%H%M%S}-{offset_token}",
        "title": f"Generated playlist · {local_time:%Y-%m-%d %H:%M:%S} ({time_zone_name})",
        "generated_at": local_time.isoformat(timespec="seconds"),
    }


def _localized(chinese, english):
    return (
        f'<span lang="zh-CN">{html.escape(chinese)}</span>'
        f'<span lang="en">{html.escape(english)}</span>'
    )


def _localized_label(value):
    parts = value.split(" / ", 1)
    return _localized(*parts) if len(parts) == 2 else html.escape(value)


def _render_page_sections(pages_base_url, copied_names, release_download_base=""):
    available_names = set(copied_names)
    sections = []
    copy_icon = (
        '<svg viewBox="0 0 20 20" aria-hidden="true">'
        '<rect x="6.5" y="6.5" width="9" height="9" rx="2"/>'
        '<path d="M4.5 13.5h-1a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1"/></svg>'
    )
    preview_icon = (
        '<svg viewBox="0 0 20 20" aria-hidden="true">'
        '<path d="M2 10s3-5.5 8-5.5 8 5.5 8 5.5-3 5.5-8 5.5S2 10 2 10Z"/>'
        '<circle cx="10" cy="10" r="2.5"/></svg>'
    )
    download_icon = (
        '<svg viewBox="0 0 20 20" aria-hidden="true">'
        '<path d="M10 2.5v9m-3.5-3.5L10 11.5 13.5 8"/>'
        '<path d="M3.5 12.5v3a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-3"/></svg>'
    )
    for group in PAGE_ASSET_GROUPS:
        cards = []
        for name, file_type, label, english_label in group["assets"]:
            if name not in available_names:
                continue
            url = f"{pages_base_url}/{name}"
            escaped_url = html.escape(url, quote=True)
            preview_url = f"{pages_base_url}/viewer.html?file={quote(name, safe='')}"
            escaped_preview_url = html.escape(preview_url, quote=True)
            download_url = (
                f"{release_download_base}/{quote(name, safe='')}"
                if release_download_base else f"./{quote(name, safe='')}"
            )
            download_attributes = (
                ' target="_blank" rel="noopener noreferrer"'
                if release_download_base else f' download="{html.escape(name, quote=True)}"'
            )
            cards.append(f"""
        <article class="result-card">
          <span class="file-type">{html.escape(file_type)}</span>
          <span class="result-name">{_localized(label, english_label)}</span>
          <code>{escaped_url}</code>
          <span class="result-actions">
            <button class="card-action copy-action" type="button" data-copy-url="{escaped_url}">
              {copy_icon}<span class="action-label">{_localized('复制链接', 'Copy link')}</span>
            </button>
            <a class="card-action preview-action" href="{escaped_preview_url}" target="_blank" rel="noopener noreferrer">
              {preview_icon}<span>{_localized('预览内容', 'Preview')}</span>
            </a>
            <a class="card-action download-action" href="{html.escape(download_url, quote=True)}"{download_attributes}>
              {download_icon}<span>{_localized('下载文件', 'Download')}</span>
            </a>
          </span>
        </article>""")
        if not cards:
            continue
        sections.append(f"""
    <section class="result-section" aria-labelledby="{group['id']}">
      <div class="section-heading">
        <p class="eyebrow">{_localized_label(group['eyebrow'])}</p>
        <h2 id="{group['id']}">{_localized_label(group['title'])}</h2>
        <p>{_localized_label(group['description'])}</p>
      </div>
      <div class="result-grid">{''.join(cards)}
      </div>
    </section>""")
    return "".join(sections)


def _render_page_viewer(copied_names):
    previewable_names = sorted(set(copied_names) & PREVIEWABLE_PAGE_ASSETS)
    allowed_json = json.dumps(previewable_names, ensure_ascii=True)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <meta name="theme-color" content="#1d4ed8">
  <link rel="icon" href="favicon.svg" type="image/svg+xml">
  <title>IPTV-API 文件预览 / File preview</title>
  <script>
    try {{
      document.documentElement.lang = localStorage.getItem("iptv-pages-language") ||
        (navigator.language.toLowerCase().startsWith("zh") ? "zh-CN" : "en");
    }} catch (error) {{ document.documentElement.lang = "zh-CN"; }}
  </script>
  <style>
    :root {{ color-scheme: light dark; --page: #eff6ff; --surface: #ffffff; --text: #172554; --muted: #475569; --border: #bfdbfe; --primary: #1d4ed8; }}
    * {{ box-sizing: border-box; }}
    html[lang="zh-CN"] [lang="en"], html[lang="en"] [lang="zh-CN"] {{ display: none; }}
    body {{ min-height: 100vh; margin: 0; background: var(--page); color: var(--text); font: 15px/1.6 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(1200px, calc(100% - 32px)); margin: 0 auto; padding: 24px 0 40px; }}
    header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; }}
    .heading {{ min-width: 0; }}
    h1 {{ margin: 0; overflow-wrap: anywhere; font-size: clamp(20px, 3vw, 28px); line-height: 1.25; }}
    .subtitle {{ margin: 2px 0 0; color: var(--muted); font-size: 13px; }}
    a {{ color: var(--primary); font-weight: 700; text-underline-offset: 3px; }}
    a:focus-visible {{ outline: 3px solid var(--primary); outline-offset: 3px; border-radius: 4px; }}
    .actions {{ display: flex; flex: none; flex-wrap: wrap; gap: 12px; }}
    .language-switch {{ display: inline-flex; gap: 3px; padding: 3px; border: 1px solid var(--border); border-radius: 999px; }}
    .language-switch button {{ min-width: 42px; padding: 3px 7px; border: 0; border-radius: 999px; background: transparent; color: var(--text); cursor: pointer; }}
    .language-switch button[aria-pressed="true"] {{ background: var(--primary); color: #ffffff; }}
    .language-switch button:focus-visible {{ outline: 3px solid var(--primary); outline-offset: 3px; }}
    pre {{ min-height: 320px; margin: 0; padding: 20px; overflow: auto; border: 1px solid var(--border); border-radius: 14px; background: var(--surface); box-shadow: 0 10px 30px rgba(30, 64, 175, 0.08); color: var(--text); font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }}
    @media (max-width: 700px) {{ main {{ width: min(100% - 24px, 1200px); padding-top: 16px; }} header {{ align-items: flex-start; flex-direction: column; }} }}
    @media (prefers-color-scheme: dark) {{ :root {{ --page: #081225; --surface: #0f1f3a; --text: #eff6ff; --muted: #cbd5e1; --border: #27446f; --primary: #93c5fd; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div class="heading">
        <h1 id="file-name">{_localized('文件预览', 'File preview')}</h1>
        <p class="subtitle">{_localized('使用 UTF-8 解码，仅用于浏览器查看；播放器仍应使用原始 Pages 链接。', 'Decoded as UTF-8 for browser viewing; players should use the original Pages URL.')}</p>
      </div>
      <nav class="actions" aria-label="预览操作 / Preview actions">
        <a href="./">{_localized('返回结果页', 'Back to results')}</a>
        <a id="raw-link" href="./">{_localized('打开原始文件', 'Raw file')}</a>
        <span class="language-switch" role="group" aria-label="Language / 语言">
          <button type="button" data-language="zh-CN" aria-pressed="true">中文</button>
          <button type="button" data-language="en" aria-pressed="false">EN</button>
        </span>
      </nav>
    </header>
    <pre id="content" aria-live="polite">{_localized('正在加载…', 'Loading…')}</pre>
  </main>
  <script>
    const allowedFiles = new Set({allowed_json});
    const fileName = new URLSearchParams(window.location.search).get("file") || "";
    const heading = document.getElementById("file-name");
    const content = document.getElementById("content");
    const rawLink = document.getElementById("raw-link");
    const languageButtons = document.querySelectorAll("[data-language]");

    function setLanguage(language) {{
      document.documentElement.lang = language;
      document.title = allowedFiles.has(fileName) ? `${{fileName}} · IPTV-API` :
        (language === "en" ? "IPTV-API File preview" : "IPTV-API 文件预览");
      languageButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.language === language)));
      try {{ localStorage.setItem("iptv-pages-language", language); }} catch (error) {{}}
      if (!allowedFiles.has(fileName)) {{
        heading.textContent = language === "en" ? "Preview unavailable" : "无法预览";
        content.textContent = language === "en" ? "The file is unavailable or cannot be previewed." : "文件不存在或不支持浏览器预览。";
      }} else if (content.dataset.failed === "true") {{
        content.textContent = language === "en" ? "Loading failed. Open the raw file or try again later." : "加载失败，请打开原始文件或稍后重试。";
      }}
    }}

    languageButtons.forEach((button) => button.addEventListener("click", () => setLanguage(button.dataset.language)));
    setLanguage(document.documentElement.lang === "en" ? "en" : "zh-CN");

    if (!allowedFiles.has(fileName)) {{
      rawLink.hidden = true;
    }} else {{
      const rawUrl = `./${{encodeURIComponent(fileName)}}`;
      heading.textContent = fileName;
      rawLink.href = rawUrl;
      fetch(rawUrl, {{ cache: "no-cache" }})
        .then((response) => {{
          if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
          return response.arrayBuffer();
        }})
        .then(async (buffer) => {{
          if (fileName === "epg.gz") {{
            if (typeof DecompressionStream === "undefined") throw new Error("gzip preview unavailable");
            buffer = await new Response(
              new Blob([buffer]).stream().pipeThrough(new DecompressionStream("gzip"))
            ).arrayBuffer();
          }}
          content.textContent = new TextDecoder("utf-8").decode(buffer);
        }})
        .catch(() => {{
          content.dataset.failed = "true";
          setLanguage(document.documentElement.lang);
        }});
    }}
  </script>
</body>
</html>
"""


def prepare_release_assets(
        final_file,
        destination,
        output_dir="output",
):
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir
    output_dir = output_dir.resolve(strict=True)

    destination = Path(destination)
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"Release destination must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    final_path = _resolve_output_file(Path(final_file), output_dir)
    if final_path.suffix.lower() != ".txt":
        raise ValueError(f"The configured final_file must use the .txt extension: {final_file}")

    sources = [(final_path, "result.txt")]
    final_m3u = final_path.with_suffix(".m3u")
    if final_m3u.exists():
        sources.append((_resolve_output_file(final_m3u, output_dir), "result.m3u"))
    for relative_path, asset_name in OPTIONAL_ASSETS.items():
        candidate = output_dir / relative_path
        if candidate.exists():
            sources.append((_resolve_output_file(candidate, output_dir), asset_name))
    for relative_path, asset_name in LOG_ASSETS.items():
        candidate = output_dir / relative_path
        if candidate.exists():
            sources.append((_resolve_output_file(candidate, output_dir, allow_empty=True), asset_name))

    seen_names = set()
    for source, asset_name in sources:
        if asset_name in seen_names:
            raise ValueError(f"Duplicate release asset name: {asset_name}")
        seen_names.add(asset_name)
        target = destination / asset_name
        shutil.copyfile(source, target)


def prepare_pages_site(
        assets_directory,
        destination,
        pages_base_url,
        favicon_path=DEFAULT_PAGE_FAVICON,
        repository="",
        generated_at="",
        release_tag="",
):
    assets_directory = Path(assets_directory).resolve(strict=True)
    destination = Path(destination)
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"Pages destination must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    pages_base_url = _normalize_http_url(pages_base_url, "Pages base URL")
    copied_names = []
    sources = sorted(
        (
            item for item in assets_directory.iterdir()
            if item.name not in LOG_ASSETS.values() or item.name in PAGE_LOG_ASSETS
        ),
        key=lambda item: item.name,
    )
    favicon_candidate = Path(favicon_path)
    if favicon_candidate.is_symlink():
        raise ValueError(f"Pages favicon must be a regular file: {favicon_path}")
    favicon_path = favicon_candidate.resolve(strict=True)
    if not favicon_path.is_file():
        raise ValueError(f"Pages favicon must be a regular file: {favicon_path}")
    total_size = favicon_path.stat().st_size
    for source in sources:
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Pages input must be a regular file: {source}")
        total_size += source.stat().st_size
    if total_size > MAX_PAGES_SITE_BYTES:
        raise ValueError("Pages input exceeds the GitHub Pages site size limit")

    for source in sources:
        shutil.copyfile(source, destination / source.name)
        copied_names.append(source.name)
    shutil.copyfile(favicon_path, destination / "favicon.svg")

    if "result.txt" not in copied_names:
        raise ValueError("Pages input must contain result.txt")

    repository = str(repository or "").strip()
    generated_at = str(generated_at or "").strip()
    release_tag = str(release_tag or "").strip()
    release_footer = ""
    release_download_base = ""
    main_repository_notice = ""
    if REPOSITORY_PATTERN.fullmatch(repository):
        if RELEASE_TAG_PATTERN.fullmatch(release_tag):
            release_download_base = f"https://github.com/{repository}/releases/download/{release_tag}"
        release_path = (
            f"releases/tag/{release_tag}"
            if RELEASE_TAG_PATTERN.fullmatch(release_tag)
            else "releases"
        )
        release_url = f"https://github.com/{repository}/{release_path}"
        release_footer = f"""<p>{_localized('本次运行存档：', 'Run archive: ')}
          <a class="inline-release-link" href="{html.escape(release_url, quote=True)}" target="_blank" rel="noopener noreferrer">Release</a></p>"""
    if repository.lower() == "guovin/iptv-api":
        fork_url = f"https://github.com/{repository}/fork"
        escaped_fork_url = html.escape(fork_url, quote=True)
        main_repository_notice = f"""
    <aside class="test-notice" role="note" aria-labelledby="test-notice-title">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2.8 20h18.4L12 3Z"/><path d="M12 9v5M12 17.5h.01"/></svg>
      <div>
        <h2 id="test-notice-title">{_localized('主仓库结果说明', 'Upstream results notice')}</h2>
        <p lang="zh-CN">主仓库发布的 Pages 链接和 Release 结果仅用于功能测试，不保证内容完整性、可用性或持续更新。实际使用请
          <a class="inline-fork-link" href="{escaped_fork_url}" target="_blank" rel="noopener noreferrer">Fork 项目</a>
          并运行自己的工作流。</p><p lang="en">Pages links and Release results from the upstream repository are for functional testing only.
          <a class="inline-fork-link" href="{escaped_fork_url}" target="_blank" rel="noopener noreferrer">Fork the project</a>
          and run your own workflow for actual use.</p>
      </div>
    </aside>"""
    generated_markup = (
        f'<time datetime="{html.escape(generated_at, quote=True)}">{html.escape(generated_at)}</time>'
        if generated_at
        else _localized("当前工作流", "Current workflow run")
    )
    sections_html = _render_page_sections(pages_base_url, copied_names, release_download_base)
    index_html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <meta name="theme-color" content="#1d4ed8">
  <meta name="description" content="IPTV-API generated playlist, IPv4, IPv6, and EPG result links.">
  <link rel="icon" href="favicon.svg" type="image/svg+xml">
  <title>IPTV-API 更新结果 / Playlist results</title>
  <script>
    try {{
      document.documentElement.lang = localStorage.getItem("iptv-pages-language") ||
        (navigator.language.toLowerCase().startsWith("zh") ? "zh-CN" : "en");
    }} catch (error) {{ document.documentElement.lang = "zh-CN"; }}
  </script>
  <style>
    :root {{
      color-scheme: light dark;
      --page: #eff6ff;
      --surface: rgba(255, 255, 255, 0.88);
      --surface-strong: #ffffff;
      --text: #172554;
      --muted: #475569;
      --border: #bfdbfe;
      --primary: #1d4ed8;
      --primary-strong: #1e3a8a;
      --primary-soft: #dbeafe;
      --shadow: 0 18px 50px rgba(30, 64, 175, 0.12);
      --radius-lg: 24px;
      --radius-md: 16px;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    html[lang="zh-CN"] [lang="en"], html[lang="en"] [lang="zh-CN"] {{ display: none; }}
    body {{
      min-height: 100vh;
      margin: 0;
      background:
        radial-gradient(circle at 10% 0%, rgba(59, 130, 246, 0.16), transparent 34rem),
        radial-gradient(circle at 95% 12%, rgba(22, 163, 74, 0.10), transparent 28rem),
        var(--page);
      color: var(--text);
      font: 16px/1.6 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      -webkit-font-smoothing: antialiased;
    }}
    a {{ color: inherit; }}
    .page-shell {{ width: min(1120px, calc(100% - 32px)); margin: 0 auto; padding: 32px 0 48px; }}
    .hero {{
      position: relative;
      overflow: hidden;
      padding: clamp(26px, 4.5vw, 48px);
      border: 1px solid rgba(255, 255, 255, 0.28);
      border-radius: var(--radius-lg);
      background: linear-gradient(135deg, #1e3a8a 0%, #1d4ed8 55%, #2563eb 100%);
      box-shadow: var(--shadow);
      color: #ffffff;
    }}
    .hero::after {{
      position: absolute;
      inset: auto -8% -60% auto;
      width: 360px;
      height: 360px;
      border: 72px solid rgba(255, 255, 255, 0.08);
      border-radius: 50%;
      content: "";
    }}
    .brand {{ display: inline-flex; align-items: center; gap: 10px; margin: 0 0 24px; font-weight: 750; letter-spacing: -0.02em; }}
    .hero-top {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }}
    .language-switch {{ display: inline-flex; gap: 3px; padding: 3px; border: 1px solid rgba(255, 255, 255, 0.45); border-radius: 999px; }}
    .language-switch button {{ min-width: 46px; padding: 5px 10px; border: 0; border-radius: 999px; background: transparent; color: #dbeafe; font: inherit; font-size: 13px; font-weight: 700; cursor: pointer; }}
    .language-switch button[aria-pressed="true"] {{ background: #ffffff; color: #1e3a8a; }}
    .language-switch button:focus-visible {{ outline: 3px solid #ffffff; outline-offset: 3px; }}
    .brand-mark {{
      width: 34px;
      height: 34px;
      border-radius: 10px;
      background: transparent;
    }}
    .hero-content {{ position: relative; z-index: 1; max-width: 1000px; }}
    h1 {{ max-width: 680px; margin: 0; font-size: clamp(34px, 5vw, 56px); line-height: 1.02; letter-spacing: -0.05em; }}
    .hero-copy {{ max-width: 100%; margin: 16px 0 0; color: #dbeafe; font-size: clamp(16px, 1.7vw, 18px); text-wrap: pretty; }}
    .hero-copy [lang="en"] {{ color: #bfdbfe; }}
    .hero-meta {{ display: flex; flex-wrap: wrap; gap: 16px 28px; margin-top: 24px; color: #bfdbfe; font-size: 13px; }}
    .hero-meta strong {{ display: block; color: #ffffff; font-size: 18px; }}
    .inline-release-link {{ color: var(--primary); font-weight: 750; text-decoration-thickness: 1.5px; text-underline-offset: 3px; }}
    .inline-release-link:hover {{ color: var(--primary-strong); }}
    .inline-fork-link {{ color: #92400e; font-weight: 750; text-decoration-thickness: 1.5px; text-underline-offset: 3px; }}
    .inline-fork-link:hover {{ color: #78350f; }}
    .inline-release-link:focus-visible, .inline-fork-link:focus-visible, .card-action:focus-visible {{ outline: 3px solid currentColor; outline-offset: 3px; border-radius: 4px; }}
    .test-notice {{ display: flex; gap: 14px; margin: 24px 0 0; padding: 18px 20px; border: 1px solid #f59e0b; border-radius: var(--radius-md); background: #fffbeb; color: #78350f; }}
    .test-notice svg {{ width: 22px; height: 22px; flex: none; margin-top: 2px; fill: none; stroke: #b45309; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.8; }}
    .test-notice h2 {{ margin: 0 0 4px; font-size: 16px; }}
    .test-notice p {{ margin: 0; font-size: 14px; }}
    .test-notice [lang="en"] {{ color: #92400e; }}
    .result-section {{ margin-top: 48px; }}
    .section-heading {{ max-width: 720px; margin-bottom: 18px; }}
    .eyebrow {{ margin: 0 0 4px; color: var(--primary); font-size: 12px; font-weight: 800; letter-spacing: 0.12em; text-transform: uppercase; }}
    .section-heading h2 {{ margin: 0; font-size: clamp(24px, 3vw, 32px); line-height: 1.2; letter-spacing: -0.035em; }}
    .section-heading > p:last-child {{ margin: 8px 0 0; color: var(--muted); }}
    .result-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
    .result-card {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 3px 16px;
      min-width: 0;
      padding: 22px;
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      background: var(--surface-strong);
      box-shadow: 0 8px 24px rgba(30, 64, 175, 0.06);
      transition: border-color 180ms ease, box-shadow 180ms ease, transform 180ms ease;
    }}
    .result-card:hover {{ border-color: var(--primary); box-shadow: 0 14px 32px rgba(30, 64, 175, 0.13); transform: translateY(-2px); }}
    .file-type {{ grid-column: 2; grid-row: 1 / span 2; align-self: start; padding: 5px 9px; border-radius: 999px; background: var(--primary-soft); color: var(--primary-strong); font-size: 11px; font-weight: 800; letter-spacing: 0.04em; }}
    .result-name {{ grid-column: 1; font-size: 17px; font-weight: 760; letter-spacing: -0.02em; }}
    .result-card code {{ grid-column: 1 / -1; min-width: 0; margin-top: 14px; padding: 10px 12px; overflow-wrap: anywhere; border-radius: 10px; background: var(--page); color: var(--primary-strong); font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    .result-actions {{ display: flex; grid-column: 1 / -1; flex-wrap: wrap; gap: 10px; margin-top: 14px; }}
    .card-action {{
      display: inline-flex;
      min-height: 44px;
      align-items: center;
      justify-content: center;
      gap: 7px;
      padding: 9px 13px;
      border: 1px solid var(--border);
      border-radius: 10px;
      font: 750 13px/1.3 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      text-decoration: none;
      cursor: pointer;
      transition: background-color 160ms ease, border-color 160ms ease, color 160ms ease;
    }}
    .card-action svg {{ width: 16px; height: 16px; flex: none; fill: none; stroke: currentColor; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.8; }}
    .copy-action {{ background: var(--surface-strong); color: var(--primary); }}
    .copy-action:hover {{ border-color: var(--primary); background: var(--primary-soft); }}
    .copy-action.is-copied {{ border-color: #16a34a; background: #dcfce7; color: #166534; }}
    .preview-action {{ border-color: var(--border); background: var(--primary-soft); color: var(--primary-strong); }}
    .preview-action:hover {{ border-color: var(--primary); background: #bfdbfe; }}
    .download-action {{ border-color: #1d4ed8; background: #1d4ed8; color: #ffffff; }}
    .download-action:hover {{ border-color: #1e40af; background: #1e40af; }}
    .sr-only {{ position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }}
    footer {{ display: flex; justify-content: space-between; gap: 24px; margin-top: 56px; padding: 24px 0 8px; border-top: 1px solid var(--border); color: var(--muted); font-size: 13px; }}
    footer p {{ margin: 0; }}
    @media (max-width: 700px) {{
      .page-shell {{ width: min(100% - 24px, 1120px); padding-top: 12px; }}
      .hero {{ padding: 24px 20px 28px; border-radius: 20px; }}
      .brand {{ margin-bottom: 22px; }}
      .result-grid {{ grid-template-columns: 1fr; }}
      .result-section {{ margin-top: 40px; }}
      .result-actions {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .download-action {{ grid-column: 1 / -1; }}
      footer {{ flex-direction: column; }}
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --page: #081225;
        --surface: rgba(15, 31, 58, 0.86);
        --surface-strong: #0f1f3a;
        --text: #eff6ff;
        --muted: #cbd5e1;
        --border: #27446f;
        --primary: #93c5fd;
        --primary-strong: #dbeafe;
        --primary-soft: #173663;
        --shadow: 0 18px 50px rgba(0, 0, 0, 0.28);
      }}
      .hero {{ background: linear-gradient(135deg, #172554 0%, #1e3a8a 58%, #1d4ed8 100%); }}
      .result-card code {{ background: #081225; color: #bfdbfe; }}
      .test-notice {{ border-color: #92400e; background: #2b1d0b; color: #fef3c7; }}
      .test-notice svg {{ stroke: #fbbf24; }}
      .test-notice [lang="en"] {{ color: #fde68a; }}
      .inline-fork-link, .inline-fork-link:hover {{ color: #fde68a; }}
      .copy-action.is-copied {{ border-color: #4ade80; background: #153d2a; color: #bbf7d0; }}
      .preview-action {{ border-color: #36537a; background: #173663; color: #dbeafe; }}
      .preview-action:hover {{ border-color: #93c5fd; background: #244976; }}
      .download-action {{ border-color: #2563eb; background: #2563eb; color: #ffffff; }}
      .download-action:hover {{ border-color: #3b82f6; background: #1d4ed8; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      html {{ scroll-behavior: auto; }}
      .result-card, .card-action {{ transition: none; }}
      .result-card:hover {{ transform: none; }}
    }}
  </style>
</head>
<body>
  <main class="page-shell">
    <header class="hero">
      <div class="hero-content">
        <div class="hero-top">
          <p class="brand"><img class="brand-mark" src="favicon.svg" alt="" width="34" height="34"> IPTV-API</p>
          <div class="language-switch" role="group" aria-label="Language / 语言">
            <button type="button" data-language="zh-CN" aria-pressed="true">中文</button>
            <button type="button" data-language="en" aria-pressed="false">EN</button>
          </div>
        </div>
        <h1>{_localized('更新结果', 'Playlist results')}</h1>
        <p class="hero-copy">{_localized('复制在线订阅地址，或预览并下载本次生成的文件。', 'Copy a subscription URL, preview a file, or download this run’s results.')}</p>
        <div class="hero-meta">
          <span><strong>{len(copied_names)}</strong>{_localized('可用文件', 'Available files')}</span>
          <span><strong>{_localized('生成时间', 'Generated')}</strong>{generated_markup}</span>
        </div>
      </div>
    </header>

    {main_repository_notice}

    {sections_html}

    <p class="sr-only" id="copy-status" role="status" aria-live="polite"></p>

    <footer>
      <p>{_localized('由 IPTV-API 生成，结果文件未提交到 Git。', 'Generated by IPTV-API without committing result files to Git.')}</p>
      {release_footer}
    </footer>
  </main>
  <script>
    const copyStatus = document.getElementById("copy-status");
    const languageButtons = document.querySelectorAll("[data-language]");

    function setLanguage(language) {{
      document.documentElement.lang = language;
      document.title = language === "en" ? "IPTV-API Playlist results" : "IPTV-API 更新结果";
      languageButtons.forEach((button) => {{
        button.setAttribute("aria-pressed", String(button.dataset.language === language));
      }});
      try {{ localStorage.setItem("iptv-pages-language", language); }} catch (error) {{}}
    }}

    languageButtons.forEach((button) => button.addEventListener("click", () => setLanguage(button.dataset.language)));
    setLanguage(document.documentElement.lang === "en" ? "en" : "zh-CN");

    function fallbackCopy(value) {{
      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      const copied = document.execCommand("copy");
      textarea.remove();
      return copied;
    }}

    async function copyLink(value) {{
      if (navigator.clipboard && window.isSecureContext) {{
        await navigator.clipboard.writeText(value);
        return true;
      }}
      return fallbackCopy(value);
    }}

    document.addEventListener("click", async (event) => {{
      const button = event.target.closest("[data-copy-url]");
      if (!button) return;

      const label = button.querySelector(".action-label");
      const originalLabel = button.dataset.originalLabel || label.innerHTML;
      button.dataset.originalLabel = originalLabel;
      window.clearTimeout(Number(button.dataset.resetTimer || 0));

      try {{
        if (!await copyLink(button.dataset.copyUrl)) throw new Error("copy failed");
        label.textContent = document.documentElement.lang === "en" ? "Copied" : "已复制";
        button.classList.add("is-copied");
        copyStatus.textContent = document.documentElement.lang === "en" ? `Copied ${{button.dataset.copyUrl}}` : `已复制 ${{button.dataset.copyUrl}}`;
      }} catch (error) {{
        label.textContent = document.documentElement.lang === "en" ? "Copy failed" : "复制失败";
        button.classList.remove("is-copied");
        copyStatus.textContent = document.documentElement.lang === "en" ? "Copy failed; select the link manually" : "复制失败，请手动选择链接";
      }}

      button.dataset.resetTimer = String(window.setTimeout(() => {{
        label.innerHTML = originalLabel;
        button.classList.remove("is-copied");
      }}, 1800));
    }});
  </script>
</body>
</html>
"""
    (destination / "index.html").write_text(index_html, encoding="utf-8")
    (destination / "viewer.html").write_text(
        _render_page_viewer(copied_names),
        encoding="utf-8",
    )
    return {
        "pages_base_url": pages_base_url,
        "assets": copied_names,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Prepare public IPTV result files for GitHub Pages and a per-run release."
    )
    parser.add_argument("--final-file", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--generated-at")
    parser.add_argument("--release-tag")
    parser.add_argument("--pages-destination")
    parser.add_argument("--pages-base-url")
    args = parser.parse_args()
    prepare_release_assets(
        final_file=args.final_file,
        destination=args.destination,
        output_dir=args.output_dir,
    )
    if bool(args.pages_destination) != bool(args.pages_base_url):
        parser.error("--pages-destination and --pages-base-url must be used together")
    if args.pages_destination:
        prepare_pages_site(
            assets_directory=args.destination,
            destination=args.pages_destination,
            pages_base_url=args.pages_base_url,
            repository=os.getenv("GITHUB_REPOSITORY"),
            generated_at=args.generated_at,
            release_tag=args.release_tag,
        )


if __name__ == "__main__":
    main()
