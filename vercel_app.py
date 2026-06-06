import os, json, re, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from io import BytesIO
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── supabase client ───────────────────────────────────────────────────────────

def get_sb():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)

# ── config helpers (Supabase DB) ──────────────────────────────────────────────

def load_cfg():
    cfg = {}
    # Non-secrets from app_config table
    sb = get_sb()
    if sb:
        try:
            r = sb.table("app_config").select("*").eq("id", 1).execute()
            if r.data:
                row = r.data[0]
                for k in ["your_name", "job_role", "auto_send", "openai_base_url", "openai_model", "cv_filename", "cv_storage_path"]:
                    v = row.get(k)
                    if v is not None:
                        cfg[k] = v
        except Exception as e:
            print(f"[DB load] {e}")
    # Secrets from env vars
    env_map = {
        "gmail_user": "GMAIL_USER", "gmail_app_password": "GMAIL_APP_PASSWORD",
        "groq_api_key": "GROQ_API_KEY", "supabase_url": "SUPABASE_URL",
        "supabase_key": "SUPABASE_KEY",
    }
    for key, env_key in env_map.items():
        val = os.getenv(env_key)
        if val:
            cfg[key] = val
    return cfg

def save_cfg(c):
    sb = get_sb()
    if not sb:
        return
    try:
        sb.table("app_config").update({
            "your_name": c.get("your_name", ""),
            "job_role": c.get("job_role", ""),
            "auto_send": bool(c.get("auto_send", False)),
            "openai_base_url": c.get("openai_base_url", "https://api.groq.com/openai/v1"),
            "openai_model": c.get("openai_model", "llama-3.3-70b-versatile"),
            "cv_filename": c.get("cv_filename", ""),
            "cv_storage_path": c.get("cv_storage_path", ""),
        }).eq("id", 1).execute()
    except Exception as e:
        print(f"[DB save] {e}")

# ── CV helpers (Supabase Storage) ─────────────────────────────────────────────

def read_cv(cfg):
    sb = get_sb()
    storage_path = cfg.get("cv_storage_path", "")
    if not sb or not storage_path:
        return ""
    try:
        data = sb.storage.from_("cvs").download(storage_path)
        ext = storage_path.rsplit(".", 1)[-1].lower() if "." in storage_path else ""
        if ext == "pdf":
            import PyPDF2
            r = PyPDF2.PdfReader(BytesIO(data))
            return " ".join(p.extract_text() or "" for p in r.pages)[:4000]
        return data.decode("utf-8", errors="ignore")[:4000]
    except Exception as e:
        print(f"[CV storage read error] {e}")
        return ""

# ── models ────────────────────────────────────────────────────────────────────

class ConfigIn(BaseModel):
    your_name: str
    job_role: str = "Software Engineer"
    auto_send: bool = False
    openai_base_url: str = "https://api.groq.com/openai/v1"
    openai_model: str = "llama-3.3-70b-versatile"

class GenerateReq(BaseModel):
    email: str

class SendReq(BaseModel):
    email: str
    subject: str
    body: str

# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/api/config")
def api_get_config():
    cfg = load_cfg()
    safe_cfg = {k: v for k, v in cfg.items() if k not in {"gmail_app_password", "groq_api_key", "supabase_key"}}
    if cfg.get("gmail_app_password"):
        safe_cfg["gmail_app_password_display"] = "••••" + cfg["gmail_app_password"][-4:]
    if cfg.get("groq_api_key"):
        safe_cfg["groq_api_key_display"] = "••••" + cfg["groq_api_key"][-4:]
    if cfg.get("supabase_key"):
        safe_cfg["supabase_key_display"] = "••••" + cfg["supabase_key"][-4:]
    if cfg.get("cv_filename"):
        safe_cfg["cv_filename"] = cfg["cv_filename"]
    return safe_cfg

@app.post("/api/config")
def api_save_config(data: ConfigIn):
    cfg = load_cfg()
    cfg.update(data.dict())
    save_cfg(cfg)
    return {"ok": True}

@app.post("/api/upload-cv")
async def api_upload_cv(file: UploadFile = File(...)):
    content = await file.read()
    ext     = file.filename.rsplit(".", 1)[-1].lower()
    import time
    storage_path = f"cv_{int(time.time())}.{ext}"
    sb = get_sb()
    if not sb:
        raise HTTPException(400, "Supabase not configured")
    sb.storage.from_("cvs").upload(storage_path, content)
    cfg = load_cfg()
    cfg["cv_storage_path"] = storage_path
    cfg["cv_filename"]     = file.filename
    save_cfg(cfg)
    print(f"[CV] uploaded to Storage: {storage_path} ({len(content)} bytes)")
    return {"ok": True, "filename": file.filename}

@app.get("/api/cv-status")
def api_cv_status():
    cfg = load_cfg()
    return {
        "filename":     cfg.get("cv_filename", ""),
        "storage_path": cfg.get("cv_storage_path", ""),
    }

@app.post("/api/generate")
def api_generate(data: GenerateReq):
    cfg = load_cfg()
    if not cfg.get("groq_api_key"):
        raise HTTPException(400, "API key not configured")

    sb = get_sb()
    if sb and sb.table("sent_emails").select("id").eq("email", data.email).execute().data:
        raise HTTPException(409, "Already sent to this address")

    cv   = read_cv(cfg)
    name = cfg.get("your_name", "Applicant")
    role = cfg.get("job_role", "Software Engineer")

    print(f"[Generate] CV text length: {len(cv)}")

    from openai import OpenAI
    client = OpenAI(
        api_key=cfg["groq_api_key"],
        base_url=cfg.get("openai_base_url", "https://api.groq.com/openai/v1")
    )
    resp = client.chat.completions.create(
        model=cfg.get("openai_model", "llama-3.3-70b-versatile"),
        messages=[{
            "role": "user",
            "content": f"""You are {name}. Write a job application email for a {role} position.

CV content:
{cv or f"Experienced {role} with strong skills in web development and software engineering."}

Write the email body ONLY. Follow this EXACT format — every line must be present:

Dear HR,

Good [morning/afternoon/evening],

[One sentence: who you are and what you bring to the {role} role — specific, direct, no fluff]

[Two or three sentences pulled directly from the CV above — mention real skills, technologies, or achievements. No bullet points. No dashes. Plain sentences only.]

[One sentence: mention your CV is attached and you welcome the opportunity to discuss further]

Thank you for your consideration.

Best regards,
{name}
[mobile from CV if available]
[portfolio/website from CV if available]


MANDATORY — MUST APPEAR EXACTLY:
- Line 1: "Dear HR,"
- Line 2: (blank)
- Line 3: "Good morning," OR "Good afternoon," OR "Good evening," (based on current hour: 5-11=morning, 12-16=afternoon, 17-21=evening, 22-4=evening)
- Line before signature: "Best regards,"
- Last line: your name + mobile + portfolio (if in CV)

Strict rules:
- Use correct time-based greeting
- NO bullet points, NO dashes, NO lists
- NO "I am writing to", NO "I am confident", NO "passionate", NO "leverage", NO "dynamic"
- Every paragraph is plain flowing sentences
- Do NOT add any extra sections or sign-off text beyond what is shown above
- Sound like a real person, not a template
- If mobile/portfolio not in CV, omit those lines

Return ONLY valid JSON, no markdown fences:
{{"subject": "short professional subject line", "body": "the full email exactly as formatted above"}}"""
        }],
        max_tokens=900,
        temperature=0.8,
    )

    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```json\s*|\s*```$", "", raw).strip()

    def sanitize_json(s):
        result = []
        in_string = False
        escaped = False
        ctrl = {'\n':'\\n', '\r':'\\r', '\t':'\\t', '\b':'\\b', '\f':'\\f'}
        for ch in s:
            if escaped:
                result.append(ch); escaped = False
            elif ch == '\\':
                result.append(ch); escaped = True
            elif ch == '"':
                result.append(ch); in_string = not in_string
            elif in_string and ord(ch) < 0x20:
                result.append(ctrl.get(ch, f'\\u{ord(ch):04x}'))
            else:
                result.append(ch)
        return ''.join(result)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = json.loads(sanitize_json(raw))

    return {"subject": parsed["subject"], "body": parsed["body"]}

@app.post("/api/send")
def api_send(data: SendReq):
    cfg = load_cfg()
    for k in ["gmail_user", "gmail_app_password", "supabase_url", "supabase_key"]:
        if not cfg.get(k):
            raise HTTPException(400, f"Missing config: {k}")

    sb = get_sb()
    if sb.table("sent_emails").select("id").eq("email", data.email).execute().data:
        raise HTTPException(409, "Already sent to this address")

    msg = MIMEMultipart()
    msg["From"]    = cfg["gmail_user"]
    msg["To"]      = data.email
    msg["Subject"] = data.subject
    msg.attach(MIMEText(data.body, "plain"))

    cv_filename  = cfg.get("cv_filename", "CV.pdf")
    storage_path = cfg.get("cv_storage_path", "")
    if storage_path and sb:
        try:
            cv_data = sb.storage.from_("cvs").download(storage_path)
            part = MIMEBase("application", "octet-stream")
            part.set_payload(cv_data)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{cv_filename}"')
            msg.attach(part)
            print(f"[Send] Attached CV from Storage: {storage_path}")
        except Exception as e:
            print(f"[WARN] CV from Storage failed: {e}")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(cfg["gmail_user"], cfg["gmail_app_password"])
        s.send_message(msg)

    sb.table("sent_emails").insert({
        "email":   data.email,
        "subject": data.subject,
        "body":    data.body,
    }).execute()

    return {"ok": True}

@app.get("/api/history")
def api_history():
    cfg = load_cfg()
    if not cfg.get("supabase_url"):
        return {"emails": []}
    r = get_sb().table("sent_emails")\
        .select("email,subject,created_at")\
        .order("created_at", desc=True).limit(100).execute()
    return {"emails": r.data}

@app.get("/api/check-email/{email:path}")
def api_check_email(email: str):
    cfg = load_cfg()
    if not cfg.get("supabase_url"):
        return {"exists": False}
    r = get_sb().table("sent_emails").select("id").eq("email", email).execute()
    return {"exists": bool(r.data)}

@app.delete("/api/history/{email:path}")
def api_history_delete(email: str):
    cfg = load_cfg()
    if not cfg.get("supabase_url"):
        raise HTTPException(400, "Supabase not configured")
    get_sb().table("sent_emails").delete().eq("email", email).execute()
    return {"ok": True}

# serve frontend (must be last)
app.mount("/", StaticFiles(directory="frontend", html=True), name="static")
