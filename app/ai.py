"""Claude API: per-platform caption generation (with image vision) and comment replies."""
import base64
import json
import mimetypes
import anthropic
from . import config

_client = None

def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client

SKIP = "[SKIP]"

# Platform rules — updated for 2026. Edit here to change caption behavior.
PLATFORM_RULES = {
    "instagram": (
        "INSTAGRAM rules: max 2,200 chars. The hook MUST be in the first 125 "
        "characters (feed truncates there). HARD LIMIT of 5 hashtags (Instagram "
        "enforces this since Dec 2025), and every hashtag must be a HIGH-SEARCH "
        "term buyers actually type: the make, the model, and market tags like "
        "#DubaiCars #LuxuryCarsDubai — never niche/creative tags (colors, specs, "
        "invented phrases). For tuner builds, hashtag the BASE model searched by "
        "buyers (e.g. Brabus 800→#G63), not the edition code; the tuner "
        "brand (#Brabus #Mansory) is fine as one tag. NO emojis. "
        "End with a call to action (DM/WhatsApp/visit)."
    ),
    "facebook": (
        "FACEBOOK REELS rules — use EXACTLY this structure:\n"
        "[Hook line naming the exact car — the first 80 characters must carry it]\n"
        "(blank line)\n"
        "[1-2 conversational sentences: standout details, exclusivity, Dubai]\n"
        "(blank line)\n"
        "[ONE engagement question ending with ?]\n"
        "(blank line)\n"
        "[2-4 hashtags: base model, one #DubaiCars-style market tag, tuner brand "
        "if relevant]\n"
        "Whole caption under 500 characters, NO emojis. Conversational enthusiast "
        "tone, never salesy."
    ),
    "tiktok": (
        "TIKTOK rules: caption UNDER 150 characters, NO emojis. Format: "
        "[Car model + exclusivity] | [Key spec 1] | [Key spec 2] | [ONE engagement "
        "question]. Example: '1 of 1 Ferrari 812 GTS by Mansory | 900 HP V12 | "
        "Forged carbon widebody | Would you drive this?'. "
        "Then a blank line and 4-5 hashtags in the caption itself, mixing niche "
        "(#Brabus #ExoticCars), trending (#CarTok #FYP), and location/base-model "
        "(#Dubai + e.g. #Ferrari812). The text part stays under 150 chars; hashtags "
        "don't count toward that."
    ),
    "youtube": (
        "YOUTUBE rules: output THREE parts in this exact layout:\n"
        "TITLE: <title>\n\nDESCRIPTION:\n<description>\n\nTAGS: <tags>\n"
        "- TITLE: maximum 100 characters. MUST start with the car make and model, "
        "then key facts (tuner/edition, 1 of 1, Dubai). Use SIMPLE, clear English "
        "that people who aren't native speakers actually search — never dramatic "
        "or rare vocabulary. BAD: 'Raw Unhinged 900 HP Violence | ... Dominates'. "
        "GOOD: 'Ferrari 812 GTS by Mansory | 1 of 1 V12 Supercar in Dubai'.\n"
        "- DESCRIPTION: between 4,000 and 5,000 characters. Plain professional "
        "English, NO emojis. First 157 characters = clear summary with the main "
        "search keywords (this is the search snippet). Then detailed sections: "
        "exterior and design, interior and craftsmanship, engine and performance, "
        "exclusivity/collector value, and the experience of owning it in Dubai. "
        "ALWAYS finish the description with EXACTLY this block:\n\n"
        "{YT_FOOTER}\n\n"
        "After the footer, end the description with 3-5 hashtags MAXIMUM (YouTube "
        "shows only the first 3 above the title; hashtag walls reduce reach): the "
        "make, the base model, #Dubai, plus at most two more relevant ones.\n"
        "- TAGS: comma-separated YouTube tags, all lowercase. 10-15 STRICTLY "
        "RELEVANT tags (~300-400 characters, max 500). Every tag must directly "
        "describe THIS video: make + model and its variants, model + dubai, "
        "make + dubai, tuner + dubai (e.g. 'mansory dubai'), 'luxury cars dubai', "
        "'exotic cars dubai', 'supercars for sale dubai', 'luxury car showroom "
        "dubai', 'your dealership name'. NO generic filler tags (plain 'supercars', "
        "'luxury cars' without dubai) — they dilute the context signal."
    ),
}

CAPTION_COLS = {"instagram": "caption_ig", "facebook": "caption_fb",
                "tiktok": "caption_tt", "youtube": "caption_yt"}

POST_TYPE_RULES = {
    "image":    "Content format: single image feed post.",
    "carousel": "Content format: carousel (multiple images) — encourage swiping through all slides.",
    "reel":     "Content format: Reel / short vertical video — caption complements a video hook; "
                "create curiosity to watch to the end.",
    "story":    "Content format: Story — VERY short (1-2 lines max), strong CTA, minimal or no hashtags.",
    "video":    "Content format: standard video post.",
}

def _text(msg) -> str:
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()

def _ask(system: str, content, max_tokens=3000) -> str:
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    msg = client().messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    return _text(msg)

def _image_block(media_path):
    mime = mimetypes.guess_type(str(media_path))[0] or ""
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        return None
    try:
        data = open(media_path, "rb").read()
    except OSError:
        return None
    if len(data) > 4_500_000:
        return None
    return {"type": "image",
            "source": {"type": "base64", "media_type": mime,
                       "data": base64.b64encode(data).decode()}}

YT_SHORTS_RULES = (
    "YOUTUBE SHORTS rules (this is a Short, NOT a long video) — output THREE "
    "parts in this exact layout:\n"
    "TITLE: <title>\n\nDESCRIPTION:\n<description>\n\nTAGS: <tags>\n"
    "- TITLE: 60-80 characters. Car make and model FIRST, then key fact "
    "(tuner, 1 of 1, Dubai). Simple searchable English, NO hashtags in the title.\n"
    "- DESCRIPTION: SHORT — between 300 and 500 characters TOTAL, NO emojis. Structure: "
    "first ~100 characters = clear hook naming the exact car; then 1-2 sentences "
    "of keyword context (make, model, tuner, Dubai, luxury car showroom); then "
    "one contact line: '{YT_SHORT_CONTACT}'; then the LAST line = 3-5 hashtags, "
    "ALWAYS including #Shorts, plus the base model tag and #Dubai (the first 3 "
    "hashtags appear above the title in the Shorts player).\n"
    "- TAGS: comma-separated, lowercase. 10-15 STRICTLY RELEVANT tags "
    "(~300-400 characters, max 500). Every tag must directly describe THIS video: "
    "make + model and its variants, model + dubai, make + dubai, tuner + dubai, "
    "luxury cars dubai, exotic cars dubai, luxury car showroom dubai, your "
    "luxury cars. NO generic filler tags (plain 'supercars', 'luxury cars', "
    "'exotic cars' without dubai) — they dilute the context signal."
)

YT_SHORT_CONTACT = "WhatsApp {CONTACT_PHONE} | {WEBSITE_URL}"

DEFAULT_YT_FOOTER = (
    "About the showroom\n"
    "A premium luxury and exotic car showroom serving clients locally and "
    "worldwide, with vehicles from leading luxury brands.\n\n"
    "Worldwide export and shipping available.\n"
    "Contact us on WhatsApp {CONTACT_PHONE} for pricing, availability and "
    "private viewings.\n\n"
    "Website: {WEBSITE_URL}\n"
    "Instagram: {IG_HANDLE}\n\n"
    "Availability may change. Contact us directly for the latest updates."
)

# Optional per-brand footers (keys must match the brand keys in the BRANDS env
# var). {CONTACT_PHONE}-style placeholders are filled from .env at runtime.
YT_FOOTERS = {}

ENTHUSIAST_TONE = """TONE RULES for video content (Reels/TikTok) — enthusiast, NOT salesman:
- BANNED phrases and anything like them: "DM me", "contact us", "available now",
  "call now", "for sale", "for viewing", "don't miss out", "last chance",
  urgency language, any CTA to buy/contact, prices, phone numbers.
- REQUIRED: exactly ONE engagement question ending with "?" that invites comments
  ("Would you drive this?", "Which detail catches your eye?").
- Use curiosity hooks ("This isn't just...") and aspiration ("Imagine driving...").
- NO emojis anywhere. Clean text only — restraint reads more premium for a luxury brand.
- Highlight in priority order: 1) exclusivity (1 of 1, limited), 2) performance
  specs, 3) design details (forged carbon, bespoke), 4) luxury craftsmanship,
  5) Dubai. Include only 2-3 key specs, never all of them.
"""

REELS_RULES = (
    "INSTAGRAM REEL rules — use EXACTLY this structure:\n"
    "[Headline: car + exclusivity angle]\n"
    "(blank line)\n"
    "[3 short feature lines — design/performance/exclusivity, plain text, no emojis]\n"
    "(blank line)\n"
    "[Aspirational statement + ONE engagement question]\n"
    "(blank line)\n"
    "[EXACTLY 5 hashtags — Instagram's hard limit. Hashtags 1-4: mix of niche "
    "(#Brabus #LuxurySupercar), trending, and location/base-model "
    "(#DubaiLuxury, #G63). Hashtag 5 MUST ALWAYS be {BRAND_HASHTAG}]"
)

CAROUSEL_TEMPLATE = """SPECIAL FORMAT for the INSTAGRAM caption — this is a carousel listing post.
Follow this structure EXACTLY (it is the showroom's fixed format):

<Car name WITHOUT model year> | <Edition/Package if applicable, else omit the pipe>

Price <AED amount> AED
Price <USD amount> USD
<ONLY if mileage is 1,000 km or more, add here: "Mileage <number with commas> km". If below 1,000 km, no mileage line at all.>

<use the EXACT "Specifications header" given in the car data — do not choose it yourself>

Exterior

<bullet lines: exterior options, one per line, no bullet symbols>

Interior

<interior options, one per line>

Additional Equipment

<other equipment/driver assistance/tech options, one per line>

Technical Specifications

<engine line>
<horsepower line>
<transmission line>
<drive type line>

<2-3 sentence closing paragraph about this specific car — elegant, no year mentioned>

{IG_HANDLE}

For any inquiry Contact us

{CONTACT_PHONE}
{CONTACT_EMAIL}
{WEBSITE_URL}

<EXACTLY 5 hashtags on one line; hashtag 5 MUST be {BRAND_HASHTAG}.
Hashtags 1-4 must be HIGH-SEARCH-VOLUME terms real buyers actually type into
Instagram: the make (#Ferrari), the model (#Ferrari296GTB), and popular market
tags (#DubaiCars #LuxuryCarsDubai #CarsForSale #SupercarsDubai). NEVER use
creative or niche tags nobody searches, like color names, spec details, or
invented phrases (#RossoCorsa #HybridSupercar #TrackWeapon are BAD examples).
For tuner/bespoke builds, hashtag the BASE model people search, not the edition
code: a Brabus 800 → #G63 #MercedesG63 (not the edition code); a Mansory 812
build → #Ferrari812 #812GTS. The tuner brand itself (#Brabus #Mansory) is fine
as one tag since people do search those>

Strict rules for this format:
- HARD LENGTH LIMIT: the ENTIRE caption must stay UNDER 2,000 characters
  (Instagram rejects over 2,200). If the car has many options, CURATE — pick only
  the most impressive ones, maximum ~7 lines per section. Never dump every option.
  Shorten the closing paragraph before cutting sections.
- This format OVERRIDES every other Instagram rule (hooks, emojis, swipe prompts,
  CTAs). The caption MUST begin directly with the "<Car name> | <Edition>" line —
  NO intro sentence, NO hook, NO emojis, NO "swipe through" line before it.
- The specifications header is provided in the car data — copy it exactly, never invent or switch it.
- NEVER mention the model year anywhere.
- NO emojis anywhere in this format.
- Use the provided AED and USD price figures exactly as given.
- Distribute the car's options sensibly across Exterior / Interior / Additional Equipment.
- Keep option names clean and title-cased; deduplicate.
"""


def _car_text(car: dict) -> str:
    """Structured car data from the dealership DB for the prompt."""
    title = car.get("title", "")
    parts = [f"Title: {title}"]
    header = "GCC Specifications" if "gcc" in title.lower() else "European Specifications"
    parts.append(f"Specifications header: {header}")
    price = car.get("price")
    if price:
        aed = f"{int(float(price)):,}"
        usd = f"{int(round(float(price) / config.USD_RATE)):,}"
        parts.append(f"Price AED: {aed}")
        parts.append(f"Price USD: {usd}")
    if car.get("mileage") is not None:
        parts.append(f"Mileage km: {int(car['mileage']):,}")
    for k, label in (("engine", "Engine"), ("horsepower", "Horsepower"),
                     ("transmission", "Transmission"), ("drive_type", "Drive type"),
                     ("exterior_color", "Exterior color"), ("interior_color", "Interior color")):
        if car.get(k):
            parts.append(f"{label}: {car[k]}")
    if car.get("featured_options"):
        parts.append(f"Featured options:\n{str(car['featured_options'])[:2500]}")
    if car.get("overview"):
        import re as _re
        ov = _re.sub(r"<[^>]+>", " ", str(car["overview"]))
        ov = _re.sub(r"\s+", " ", ov).strip()
        parts.append(f"Overview: {ov[:1200]}")
    return "\n".join(parts)


def generate_captions(notes: str, media_type: str, media_path=None,
                      platforms=None, post_type: str = "image",
                      page_text: str = "", voice: str = "",
                      car_data: dict = None, brand: str = "main") -> dict:
    """Returns {platform: caption} for the requested platforms."""
    platforms = [p for p in (platforms or ["instagram", "facebook"])
                 if p in PLATFORM_RULES]

    def _rule(p):
        if p == "youtube" and post_type == "reel":
            return YT_SHORTS_RULES  # Shorts get their own compact format
        return PLATFORM_RULES[p]

    rules = "\n\n".join(_rule(p) for p in platforms)
    keys = ", ".join(f'"{p}": "..."' for p in platforms)
    carousel_block = ""
    price_rule = ("NEVER mention prices even if the listing page shows one.")
    if car_data and post_type == "carousel" and "instagram" in platforms:
        carousel_block = "\n\n" + CAROUSEL_TEMPLATE
        price_rule = ("Prices may ONLY appear in the Instagram carousel format "
                      "(using the exact provided figures); never in other captions.")
        # the fixed template fully replaces the generic Instagram style rules
        rules = "\n\n".join(_rule(p) for p in platforms if p != "instagram")
    brand_block = ""
    _fb_link = config.BRANDS.get(brand, {}).get("fb_prefix_link", "")
    if _fb_link and "facebook" in platforms:
        brand_block = (
            "\n\nBRAND FACEBOOK RULE: the Facebook caption MUST start with this "
            "exact link as its very first line, followed by a blank line, then "
            f"the hook:\n{_fb_link}"
        )
    tone_block = ""
    if post_type in ("reel", "video"):
        tone_block = "\n\n" + ENTHUSIAST_TONE
        if post_type == "reel" and "instagram" in platforms and not carousel_block:
            # reels use their own structure instead of the generic IG rules
            other = "\n\n".join(_rule(p) for p in platforms if p != "instagram")
            rules = (REELS_RULES + ("\n\n" + other if other else ""))
    system = (
        f"You write social media captions. Brand voice: {voice}\n\n"
        "IMPORTANT: the caption must be about the SPECIFIC car shown in the "
        "image, described in the notes, or detailed in the car listing page text "
        "— identify the exact car (make, model, trim, color, notable details) "
        "and write about THAT car. Never write a generic caption about the "
        f"showroom or brand in general. {price_rule}\n\n"
        f"{'' if carousel_block else POST_TYPE_RULES.get(post_type, '')}\n\n"
        f"Per-platform rules:\n{rules}"
        f"{carousel_block}{tone_block}{brand_block}\n\n"
        f"Reply ONLY with JSON: {{{keys}}}. Escape newlines inside strings as \\n."
    )
    content = []
    if media_path:
        img = _image_block(media_path)
        if img:
            content.append(img)
    user_text = (f"Media type: {media_type}\n"
                 f"Creator notes: {notes or '(none)'}")
    if car_data:
        user_text += f"\n\nCar data from the dealership database (authoritative):\n{_car_text(car_data)}"
    elif page_text:
        user_text += f"\n\nCar listing page content (extract the car's details from this):\n{page_text}"
    content.append({"type": "text", "text": user_text})
    _b = config.BRANDS.get(brand, {})
    system = (system
              .replace("{YT_FOOTER}", YT_FOOTERS.get(brand, DEFAULT_YT_FOOTER))
              .replace("{YT_SHORT_CONTACT}", YT_SHORT_CONTACT)
              .replace("{CONTACT_PHONE}", config.CONTACT_PHONE)
              .replace("{CONTACT_EMAIL}", config.CONTACT_EMAIL)
              .replace("{WEBSITE_URL}", config.WEBSITE_URL)
              .replace("{IG_HANDLE}", config.IG_HANDLE)
              .replace("{BRAND_HASHTAG}", _b.get("hashtag") or "#YourDealership"))
    text = _ask(system, content, max_tokens=8000)  # room for 5,000-char YT descriptions
    return _parse_captions(text, platforms)


def _unescape(s: str) -> str:
    return (s.replace("\\n", "\n").replace("\\t", "\t")
             .replace('\\"', '"').replace("\\\\", "\\"))


def _parse_captions(text: str, platforms) -> dict:
    """Parse the model's JSON, tolerating raw newlines inside strings."""
    import re
    # 1) strict JSON
    try:
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        return {p: str(data.get(p, "")) for p in platforms}
    except Exception:
        pass
    # 2) per-key regex extraction (handles unescaped newlines in strings)
    out = {}
    for p in platforms:
        m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % p, text)
        if m:
            out[p] = _unescape(m.group(1)).strip()
    if out:
        return {p: out.get(p, "") for p in platforms}
    # 3) last resort: raw text
    return {p: text for p in platforms}

def generate_dm_reply(platform: str, sender: str, message: str,
                      product: str = "", inventory_info: str = "",
                      voice: str = "") -> str:
    """DM auto-reply. Returns reply text or SKIP."""
    system = (
        f"You handle direct messages for a luxury car dealership. Brand voice: {voice}\n"
        f"Message source: {platform}.\n"
        "STRICT RULES:\n"
        "- Maximum 1-2 SHORT sentences. Never start a long conversation.\n"
        f"- ALWAYS end the reply with: For more details please contact us on {config.WHATSAPP_LINK}\n"
        "- Contact info: ONLY the WhatsApp link/phone number — NEVER an email address.\n"
        "- Availability questions: if inventory info below clearly shows the car is "
        "listed, say yes it's available; if it clearly shows it's gone, say it's no "
        "longer available; if unclear, don't claim either way — just direct to WhatsApp.\n"
        "- NEVER discuss or quote prices, discounts, or negotiate. Direct to WhatsApp.\n"
        f"- Reply with exactly {SKIP} (nothing else) for ANY of these:\n"
        "  * people sharing their own reels/videos/posts or asking us to view content\n"
        "  * beggars or jokers ('gift me a car', 'give me one free', etc.)\n"
        "  * offers to consign, sell us, or trade-in their car (we NEVER buy cars)\n"
        "  * promotion/collaboration/marketing/influencer offers\n"
        "  * bot messages, generic spam, scams, link spam, crypto, etc.\n"
        "  * anything that is not a genuine buyer inquiry about our cars/showroom\n"
        "- Match the sender's language (Arabic/English/etc.).\n"
        "- Output ONLY the reply text."
    )
    user = f"Sender: {sender or 'unknown'}\nMessage: {message}"
    if product:
        user += f"\nAbout marketplace listing/product: {product}"
    if inventory_info:
        user += f"\nInventory check result (from our website):\n{inventory_info[:2500]}"
    return _ask(system, user, max_tokens=250)


def split_yt(text: str) -> dict:
    """Split a YouTube caption blob into title / description / tags."""
    import re
    out = {"title": "", "desc": "", "tags": ""}
    if not text:
        return out
    m = re.search(r"TITLE:\s*(.+?)(?:\n|$)", text)
    if m:
        out["title"] = m.group(1).strip()
    m = re.search(r"DESCRIPTION:\s*\n?(.*?)(?:\n\s*TAGS:|$)", text, re.S)
    if m:
        out["desc"] = m.group(1).strip()
    m = re.search(r"TAGS:\s*(.*)$", text, re.S)
    if m:
        out["tags"] = m.group(1).strip()
    if not any(out.values()):
        out["desc"] = text.strip()
    return out


def join_yt(title: str, desc: str, tags: str) -> str:
    return f"TITLE: {title.strip()}\n\nDESCRIPTION:\n{desc.strip()}\n\nTAGS: {tags.strip()}"


def generate_reply(platform: str, commenter: str, comment_text: str,
                   voice: str = "", post_context: str = "") -> str:
    """Returns reply text, or SKIP if the comment should not be answered."""
    context_rule = ""
    if post_context:
        context_rule = (
            f"\nThe post being commented on:\n\"{post_context[:1200]}\"\n"
            "- Base your reply on THIS post's actual content. If the commenter "
            "claims something that contradicts it (wrong brand, wrong tuner, wrong "
            "model), gently state the correct fact from the post instead of "
            "agreeing. NEVER confirm details that the post content doesn't support.\n"
        )
    system = (
        f"You reply to comments on the creator's {platform} posts. Brand voice: {voice}\n"
        f"{context_rule}"
        "Rules:\n"
        "- Keep replies short (1-2 sentences), warm, natural. Match the commenter's language.\n"
        "- Never promise anything, never argue.\n"
        "- Contact info: give ONLY the phone/WhatsApp number as contact — NEVER an "
        "email address, even if the brand voice mentions one.\n"
        f"- If the comment is spam, hateful, sexual, a scam, or needs a real human decision "
        f"(business deals, complaints, legal/medical), reply with exactly {SKIP} and nothing else.\n"
        "- Output ONLY the reply text, no quotes, no explanations."
    )
    user = f"Commenter: {commenter or 'unknown'}\nComment: {comment_text}"
    return _ask(system, user, max_tokens=300)
