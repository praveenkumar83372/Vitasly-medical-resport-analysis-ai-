"""
==========================================================
  Vitalsy Elite AI  —  brain.py  v4.0
  Blood Report Analysis Backend  (FastAPI + Groq)

  pip install fastapi uvicorn groq firebase-admin
              python-docx fpdf2 pdfplumber openpyxl
              python-multipart python-dotenv

  RUN locally:  python brain.py
  RUN on Render: uvicorn brain:app --host 0.0.0.0 --port 10000
==========================================================
"""

import io, os, re, json, traceback, csv, base64
import pdfplumber
from groq import Groq
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse, FileResponse
from pydantic import BaseModel
from datetime import datetime, timezone
from fpdf import FPDF
from docx import Document

load_dotenv()

GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
FIREBASE_CRED = os.getenv("FIREBASE_CRED", "serviceAccountKey.json")

if not GROQ_API_KEY:
    raise RuntimeError(
        "\n\n❌  GROQ_API_KEY not found!\n"
        "    Add it as an Environment Variable on Render.\n"
        "    Or create a .env file locally with: GROQ_API_KEY=your_key\n"
    )

groq_client       = Groq(api_key=GROQ_API_KEY)
GROQ_MODEL        = "llama-3.3-70b-versatile"
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

SYSTEM_PROMPT = """
You are Vitalsy Elite AI — a sharp, warm, and experienced medical specialist built by the Vitalsy team.

=== WHO YOU ARE ===
- AI medical assistant specialising exclusively in blood test report analysis.
- Built by the Vitalsy team to make healthcare understandable for everyone — educated or not.
- Version 1. More report types (MRI, X-ray, urine, ECG) coming soon.
- If asked who you are, who built you, what you do, what is special about you — answer naturally and confidently, like a real person would.

=== RESPONSE STYLE — READ THIS CAREFULLY ===
- Write like a real doctor texting a patient. Not an essay. Not a wall of paragraphs.
- Mix short sentences, bullet points, and brief paragraphs naturally — never use only one format.
- NEVER write more than 2 sentences in a row without a line break or a point.
- SHORT responses for greetings, simple questions, and clarifications. Max 4-6 lines.
- STRUCTURED responses only for actual blood report analysis.
- If a response is long (like a full report analysis), SPLIT it into clearly labelled sections with a blank line between each. Like chapters, not one big block.
- Never use **, ##, ---, or markdown. Use plain text with natural spacing.
- Do not repeat the same format every reply. Feel the conversation and adapt.

=== GREETING ===
When user says hi/hello/hey:
- Greet them in ONE warm sentence.
- Then in 2-3 short lines (not a list), mention what they can do: upload a report, describe symptoms, or ask questions.
- Total response: max 5 lines. No more.

Example of GOOD greeting:
"Hey! Good to have you here. I am Vitalsy AI — your blood report specialist.
You can upload a blood report and I will break it down for you, or just describe what you are feeling and we can go from there."

=== WHEN ASKED WHAT YOU CAN DO ===
Give a short, punchy list. Not paragraphs.
Example:
"Here is what I can help with:

- Read and explain your blood test report in plain language
- Flag values that are High, Low, or Normal
- Tell you what each result means for your body
- Give you diet and lifestyle tips based on your results
- Answer any questions about blood tests

Just upload your report or describe your symptoms and we will get started."

=== BLOOD REPORT ANALYSIS ===
When you get blood report data (uploaded file or typed values):

Start with 1-2 sentences on the overall picture. Reassure if mostly normal.

Then go parameter by parameter like this:

Test Name
  Value: X  |  Normal Range: Y  |  Status: High / Low / Normal
  What it means: (1 plain English sentence)
  Tip: (1 practical action if abnormal — skip if normal)

After all parameters, add these FOUR sections — all mandatory:

  OVERALL SUMMARY
  3-4 plain sentences about the person overall health picture. Reassure if mostly normal.

  WHAT TO DO NEXT
  3-5 practical bullet points based on the results.

  FOOD & NUTRITION RECOMMENDATIONS
  - Foods to eat more of (with reason based on their values)
  - Foods to reduce or avoid (with reason)
  - A simple daily meal idea (breakfast, lunch, dinner)

  LIFESTYLE TIPS
  3-4 specific habits based on their results (sleep, exercise, hydration, stress)

End with: "Please see a doctor before making any medical decisions based on this."

=== CLARIFICATION ===
If user describes symptoms without a report:
- First acknowledge what they said in 1 sentence.
- Then ask: age, gender, how long, any known conditions — naturally, not as a form.

=== SCOPE — BLOOD REPORTS ONLY ===
If user uploads or asks about non-blood reports (MRI, X-ray, urine, ECG, prescription, dental etc.):
Reply: "I am sorry, right now I can only analyse blood test reports. We are working hard to add more very soon — stay tuned! Sorry for the inconvenience and have a great day."

=== FOOD & NUTRITION FOLLOW-UP ===
If user asks about food after a blood report was already analysed in this conversation:
- Do NOT ask questions. You already have their data.
- Immediately give specific foods to eat more, foods to reduce, and a simple daily meal idea.
- Name actual foods, not just "eat healthy".

If no blood report in conversation yet:
- Give short general healthy eating advice (5-6 lines max).
- Mention they can share their report for personalised advice.

=== RULES ===
- Never diagnose. Use: this may suggest, this could indicate, this is often seen in.
- If unsure — say so honestly and suggest seeing a doctor.
- Never be dismissive or cold.
- Keep follow-up answers connected to the conversation context.
- Every reply should feel like it was written by a real person, not copy-pasted from a template.
"""

# ─────────────────────────────────────────────
# FIREBASE
# ─────────────────────────────────────────────
db = None
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    if not firebase_admin._apps:
        RENDER_CRED = "/etc/secrets/serviceAccountKey.json"
        cred_path   = RENDER_CRED if os.path.exists(RENDER_CRED) else FIREBASE_CRED
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            db = firestore.client()
except Exception as e:
    print(f"Firebase not connected: {e}")

def save_chat(user_id, user_msg, ai_msg):
    if not db or user_id == "guest":
        return
    try:
        ref = db.collection("chats").document(user_id).collection("messages")
        ts  = datetime.now(timezone.utc)
        ref.add({"role": "user",      "content": user_msg, "timestamp": ts})
        ref.add({"role": "assistant", "content": ai_msg,   "timestamp": ts})
    except Exception as e:
        print("Firebase write error:", e)

# ─────────────────────────────────────────────
# FASTAPI
# ─────────────────────────────────────────────
app = FastAPI(title="Vitalsy Elite AI", version="4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:  str
    user_id:  str  = "guest"
    history:  list = []

class AnalyzeRequest(BaseModel):
    text:    str
    user_id: str = "guest"

class PDFRequest(BaseModel):
    patient_name: str  = "Patient"
    conversation: list

SUPPORTED_EXTS = {".pdf",".docx",".doc",".txt",".csv",".xlsx",".xls",".rtf",".xml",".json",".png",".jpg",".jpeg"}
BLOCKED_EXTS   = {".mp3",".mp4",".wav",".avi",".mov",".mkv",".aac",".flac",".ogg",".wma",".wmv",".webm",".m4a",".m4v"}

# ─────────────────────────────────────────────
# AI HELPERS
# ─────────────────────────────────────────────
def normalise_history(history: list) -> list:
    messages = []
    for turn in history:
        role = turn.get("role", "user")
        if "content" in turn and turn["content"]:
            text = turn["content"]
        elif "parts" in turn:
            parts = turn["parts"]
            text = parts[0] if isinstance(parts, list) and parts else str(parts)
        else: continue
        if not text or not str(text).strip(): continue
        groq_role = "assistant" if role in ("model", "assistant") else "user"
        messages.append({"role": groq_role, "content": str(text).strip()})
    return messages

def call_groq(message: str, history: list, max_tokens: int = 2000) -> str:
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(normalise_history(history))
        messages.append({"role": "user", "content": message})
        resp = groq_client.chat.completions.create(
            model=GROQ_MODEL, messages=messages, max_tokens=max_tokens, temperature=0.72
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        traceback.print_exc()
        return "I am having a little trouble connecting right now. Please try again in a moment."

# ─────────────────────────────────────────────
# FILE EXTRACTION
# ─────────────────────────────────────────────
def extract_text(file_bytes: bytes, filename: str) -> str:
    fname = filename.lower()
    if fname.endswith(".pdf"):
        parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t: parts.append(t)
        return "\n".join(parts)
    if fname.endswith((".docx", ".doc")):
        doc = Document(io.BytesIO(file_bytes))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if fname.endswith((".xlsx", ".xls")):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
            rows = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    r = "\t".join(str(c) if c is not None else "" for c in row)
                    if r.strip(): rows.append(r)
            return "\n".join(rows)
        except: return ""
    if fname.endswith(".csv"):
        text = file_bytes.decode("utf-8", errors="ignore")
        try:
            reader = csv.reader(io.StringIO(text))
            return "\n".join(", ".join(row) for row in reader)
        except: return text
    if fname.endswith((".png", ".jpg", ".jpeg")):
        return "[IMAGE_FILE]"
    return file_bytes.decode("utf-8", errors="ignore")

def analyse_image_with_groq(image_bytes: bytes, filename: str) -> str:
    ext       = os.path.splitext(filename)[1].lower().lstrip(".")
    mime_type = "image/png" if ext == "png" else "image/jpeg"
    b64       = base64.standard_b64encode(image_bytes).decode("utf-8")
    vision_instruction = "You are a medical blood report reading specialist. Extract values and analyze as per rules."

    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                    {"type": "text", "text": vision_instruction}
                ]
            }],
            max_tokens=2500,
        )
        return resp.choices[0].message.content.strip()
    except:
        return "I had trouble processing your image. Please try again."

def clean_for_pdf(text: str) -> str:
    text = text.encode("latin-1", "replace").decode("latin-1")
    text = re.sub(r'\*|#|_', '', text)
    return text.strip()

def build_pdf(patient_name: str, conversation: list) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, f"Vitalsy Report - {patient_name}", ln=True, align="C")
    pdf.ln(10)
    for msg in conversation:
        role = "Assistant" if msg.get("role") in ("assistant", "model") else "User"
        content = clean_for_pdf(msg.get("content", ""))
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 6, f"{role}:", ln=True)
        pdf.set_font("Arial", size=10)
        pdf.multi_cell(0, 5, content)
        pdf.ln(2)
    return bytes(pdf.output())

# ─────────────────────────────────────────────
# FIXED FILE SERVING (UPDATED ONLY THIS PART)
# ─────────────────────────────────────────────
def get_file_path(filename: str):
    """Checks various locations to find the HTML file."""

    # 1. templates folder (MAIN FIX 🔥)
    templates_path = os.path.join(os.getcwd(), "templates", filename)
    if os.path.exists(templates_path):
        return templates_path

    # 2. Current working directory
    cwd_path = os.path.join(os.getcwd(), filename)
    if os.path.exists(cwd_path):
        return cwd_path

    # 3. Script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, filename)
    if os.path.exists(script_path):
        return script_path

    # 4. Render path
    render_path = os.path.join("/opt/render/project/src", "templates", filename)
    if os.path.exists(render_path):
        return render_path

    return None

# ─────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────
@app.get("/api/status")
def api_status(): return {"status": "Vitalsy Elite AI is running"}

@app.post("/chat")
async def chat(request: ChatRequest):
    reply = call_groq(request.message, request.history)
    save_chat(request.user_id, request.message, reply)
    return {"reply": reply}

@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    user_id: str = Form("guest"),
    history: str = Form("[]"),
):
    file_bytes = await file.read()
    text = extract_text(file_bytes, file.filename)
    if text == "[IMAGE_FILE]":
        analysis = analyse_image_with_groq(file_bytes, file.filename)
    else:
        analysis = call_groq(f"Analyze this blood report: {text[:5000]}", json.loads(history))
    save_chat(user_id, f"[File: {file.filename}]", analysis)
    return {"analysis": analysis}

@app.post("/download-pdf")
async def download_pdf(request: PDFRequest):
    pdf_bytes = build_pdf(request.patient_name, request.conversation)
    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=report.pdf"})

# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    # THE NAME HERE MUST MATCH THE FILENAME (brain.py)
    uvicorn.run("brain:app", host="0.0.0.0", port=port, reload=False)