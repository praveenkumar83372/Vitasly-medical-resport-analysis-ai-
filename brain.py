"""
==========================================================
  Vitalsy Elite AI  —  brain.py  v4.0
  Blood Report Analysis Backend  (FastAPI + Groq)

  pip install fastapi uvicorn groq firebase-admin
              python-docx fpdf2 pdfplumber openpyxl
              python-multipart python-dotenv

  RUN:  python brain.py
==========================================================
"""

import io, os, re, json, traceback, csv, base64
import pdfplumber
from groq import Groq
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
        "    Create a .env file in your project folder with:\n"
        "    GROQ_API_KEY=your_key_here\n"
        "    Get a free key at: https://console.groq.com\n"
    )

groq_client = Groq(api_key=GROQ_API_KEY)
GROQ_MODEL        = "llama-3.3-70b-versatile"
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"  # reads images

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

Example of BAD greeting (too long, too paragraph-heavy):
"It's lovely to meet you. I'm here to help you understand your blood test results in a way that's easy to grasp. If you have a blood report you'd like me to look at, feel free to share it with me and I'll do my best to break it down for you. Alternatively, if you're experiencing some symptoms..."
— This is what you must NEVER do.

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

After all parameters, add two short sections:
  Summary — 3-4 lines. What is the overall picture?
  What to do next — 3-5 bullet points. Practical lifestyle/diet actions.

End with: "Please see a doctor before making any medical decisions based on this."

If report is unclear or incomplete — ask for the missing details before analysing.

=== CLARIFICATION ===
If user describes symptoms without a report:
- First acknowledge what they said in 1 sentence.
- Then ask: age, gender, how long, any known conditions — but ask naturally, not as a form.

=== SCOPE — BLOOD REPORTS ONLY ===
If user uploads or asks about non-blood reports (MRI, X-ray, urine, ECG, prescription, dental etc.):
Reply exactly: "I am sorry, right now I can only analyse blood test reports. We are working hard to add more very soon — stay tuned! Sorry for the inconvenience and have a great day."
Do not attempt to analyse anything else.

=== FOOD & NUTRITION FOLLOW-UP ===
If a user asks about food, diet, nutrition, or what to eat — after you have already analysed their blood report in this conversation:
- Do NOT ask them questions. You already have their report data.
- Immediately give specific food recommendations based on their blood values from earlier in the conversation.
- Format: Foods to eat more, Foods to reduce, a simple daily meal idea.
- Be specific — name actual foods, not just "eat healthy".

If a user asks about food with NO prior blood report in the conversation:
- Give general healthy eating advice in a short, friendly way.
- Mention that if they share their blood report, you can give personalised recommendations.
- Keep it to 5-6 lines max. Do not ask multiple clarifying questions.

=== RULES ===
- Never diagnose. Use: this may suggest, this could indicate, this is often seen in.
- If unsure — say so honestly and suggest seeing a doctor.
- Never be dismissive or cold.
- Keep follow-up answers connected to the conversation context — especially blood report data already shared.
- Every reply should feel like it was written by a real person, not copy-pasted from a template.
"""

db = None
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    if not firebase_admin._apps:
        # Render stores secret files in /etc/secrets/
        # Fallback to local path for development
        RENDER_CRED = "/etc/secrets/serviceAccountKey.json"
        cred_path   = RENDER_CRED if os.path.exists(RENDER_CRED) else FIREBASE_CRED
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            db = firestore.client()
            print(f"Firebase connected via: {cred_path}")
        else:
            print("Firebase cred file not found — chat history disabled.")
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

app = FastAPI(title="Vitalsy Elite AI", version="4.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class ChatRequest(BaseModel):
    message:  str
    user_id:  str  = "guest"
    history:  list = []   # full conversation: [{role, content | parts}]

class AnalyzeRequest(BaseModel):
    text:    str
    user_id: str = "guest"

class PDFRequest(BaseModel):
    patient_name: str  = "Patient"
    conversation: list

SUPPORTED_EXTS = {".pdf",".docx",".doc",".txt",".csv",".xlsx",".xls",".rtf",".xml",".json",".png",".jpg",".jpeg"}
BLOCKED_EXTS   = {".mp3",".mp4",".wav",".avi",".mov",".mkv",".aac",".flac",".ogg",".wma",".wmv",".webm",".m4a",".m4v"}

def normalise_history(history: list) -> list:
    """
    Accept history in any format the frontend sends and convert to clean Groq messages.
    Supports:
      {"role": "user",      "parts": ["text"]}   <- our own format
      {"role": "assistant", "content": "text"}   <- standard openai format
      {"role": "model",     "parts": ["text"]}   <- gemini-style
    """
    messages = []
    for turn in history:
        role = turn.get("role", "user")
        # Resolve content — try "content" first, then "parts"
        if "content" in turn and turn["content"]:
            text = turn["content"]
        elif "parts" in turn:
            parts = turn["parts"]
            text = parts[0] if isinstance(parts, list) and parts else str(parts)
        else:
            continue
        if not text or not str(text).strip():
            continue
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
        except Exception:
            return ""
    if fname.endswith(".csv"):
        text = file_bytes.decode("utf-8", errors="ignore")
        try:
            reader = csv.reader(io.StringIO(text))
            return "\n".join(", ".join(row) for row in reader)
        except Exception:
            return text
    if fname.endswith((".png", ".jpg", ".jpeg")):
        return "[IMAGE_FILE]"
    return file_bytes.decode("utf-8", errors="ignore")

def analyse_image_with_groq(image_bytes: bytes, filename: str) -> str:
    """Send image directly to Groq vision model and get blood report analysis."""
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
    mime_type = mime_map.get(ext, "image/jpeg")
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    # Dedicated vision prompt — no system prompt interference, very direct instruction
    vision_instruction = """You are a medical blood report reading specialist.

TASK: Read every single value visible in this blood report image and perform a full analysis.

RULES:
- Extract ALL test names and their values from the image — even if slightly blurry, do your best to read them.
- Do NOT say the image is unclear or ask for a better version. Just read what you can see and work with it.
- If a specific value is truly unreadable, note it briefly then continue with the rest.
- Present the full analysis in this format for each parameter:

  Test Name
  Value: X  |  Normal Range: Y  |  Status: High / Low / Normal
  Meaning: (1 plain English sentence)
  Tip: (1 practical action if abnormal — skip if normal)

- After all parameters, write these FOUR sections — all are mandatory, do not skip any:

  OVERALL SUMMARY
  3-4 plain sentences about the person's overall health picture. Reassure if mostly normal.

  WHAT TO DO NEXT
  3-5 practical bullet points based on the results.

  FOOD & NUTRITION RECOMMENDATIONS
  Give specific food recommendations tailored to this person's blood report results.
  - List foods they SHOULD eat more of, with a reason for each (based on their specific values)
  - List foods they should REDUCE or avoid, with a reason
  - Give a simple daily meal suggestion (breakfast, lunch, dinner idea)
  - Make it practical and easy to follow for someone with no nutrition background

  LIFESTYLE TIPS
  3-4 specific lifestyle habits based on their results (sleep, exercise, hydration, stress etc.)

- End with: Please consult a qualified doctor before making any medical decisions.

START the analysis immediately. Do not say you cannot read the image."""

    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64}"}
                        },
                        {
                            "type": "text",
                            "text": vision_instruction
                        }
                    ]
                }
            ],
            max_tokens=2500,
            temperature=0.5,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        traceback.print_exc()
        return f"I had trouble processing your image right now. Please try again, or type your blood values directly in the chat and I will analyse them fully."


def clean_for_pdf(text: str) -> str:
    replacements = {
        "\u2b24": "", "\U0001f534": "[HIGH]", "\U0001f7e1": "[LOW]", "\U0001f7e2": "[NORMAL]",
        "\u2705": "", "\u26a0\ufe0f": "[!]", "\u274c": "[X]",
        "\U0001f600": "", "\U0001f601": "", "\U0001f602": "", "\U0001f603": "",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    text = text.encode("latin-1", "replace").decode("latin-1")
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'_{1,2}(.*?)_{1,2}', r'\1', text)
    return text.strip()

def build_pdf(patient_name: str, conversation: list) -> bytes:
    now = datetime.now()

    ai_messages = [m.get("content","") for m in conversation if m.get("role") in ("assistant","model")]
    report_body = "\n\n".join(ai_messages)

    summary_prompt = (
        f"Based on this blood report consultation, generate a professional medical report with these sections:\n"
        f"1. Overall Summary\n2. Parameter Analysis (each value, status, plain English meaning)\n"
        f"3. Key Concerns (if any)\n4. Nutrition and Lifestyle Recommendations\n5. Doctor Note\n\n"
        f"Patient name: {patient_name}\n"
        f"Consultation content:\n{report_body[:4000]}\n\n"
        f"Write as a professional medical document. No markdown. Plain text with clear section headings only. "
        f"For each parameter write Normal Range and Patient Value on separate lines."
    )
    structured = clean_for_pdf(call_groq(summary_prompt, [], max_tokens=2000))

    DARK_BLUE  = (0,  51, 102)
    MID_BLUE   = (0,  82, 142)
    LIGHT_BLUE = (230, 242, 255)
    WHITE      = (255, 255, 255)
    DARK_GRAY  = (50,  50,  50)
    MID_GRAY   = (100, 100, 100)
    LINE_GRAY  = (210, 210, 210)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=32)
    pdf.add_page()

    # Header band
    pdf.set_fill_color(*DARK_BLUE)
    pdf.rect(0, 0, 210, 40, 'F')
    pdf.set_xy(10, 8)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Arial", "B", 24)
    pdf.cell(130, 12, "Vitalsy Elite AI", ln=False)
    pdf.set_font("Arial", "I", 9)
    pdf.set_xy(10, 22)
    pdf.cell(130, 6, "Intelligent Blood Report Analysis Platform", ln=False)
    pdf.set_xy(140, 9)
    pdf.set_font("Arial", "B", 11)
    pdf.cell(60, 7, "MEDICAL REPORT", align="R", ln=True)
    pdf.set_xy(140, 17)
    pdf.set_font("Arial", size=8)
    pdf.cell(60, 5, f"Date: {now.strftime('%d %B %Y')}", align="R", ln=True)
    pdf.set_xy(140, 23)
    pdf.cell(60, 5, f"Time: {now.strftime('%I:%M %p')}", align="R", ln=True)

    # Patient info bar
    pdf.set_fill_color(*LIGHT_BLUE)
    pdf.rect(0, 40, 210, 16, 'F')
    pdf.set_text_color(*DARK_BLUE)
    pdf.set_font("Arial", "B", 10)
    pdf.set_xy(10, 44)
    pdf.cell(100, 6, f"Patient:  {patient_name}", ln=False)
    pdf.set_font("Arial", size=8)
    pdf.set_text_color(*MID_GRAY)
    pdf.set_x(110)
    pdf.cell(90, 6, "Powered by Vitalsy Elite AI  |  vitalsy.ai", align="R", ln=True)
    pdf.ln(6)

    # Divider
    pdf.set_draw_color(*MID_BLUE)
    pdf.set_line_width(0.6)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)

    # Report body
    SECTION_KW = ["summary","analysis","parameter","concern","nutrition","lifestyle","recommendation","note","doctor"]
    for line in structured.split("\n"):
        line = line.strip()
        if not line:
            pdf.ln(2)
            continue
        ll = line.lower()
        is_section = any(kw in ll for kw in SECTION_KW) and len(line) < 80
        is_status  = any(t in line for t in ("[HIGH]","[LOW]","[NORMAL]"))

        if is_section:
            pdf.ln(3)
            pdf.set_fill_color(*MID_BLUE)
            pdf.set_text_color(*WHITE)
            pdf.set_font("Arial", "B", 10)
            pdf.cell(0, 7, f"   {line.upper()}", ln=True, fill=True)
            pdf.ln(1)
            pdf.set_text_color(*DARK_GRAY)
        elif is_status:
            if "[HIGH]" in line:   pdf.set_fill_color(255, 230, 230)
            elif "[LOW]" in line:  pdf.set_fill_color(255, 250, 215)
            else:                  pdf.set_fill_color(225, 255, 225)
            pdf.set_font("Arial", "B", 9)
            pdf.set_text_color(*DARK_GRAY)
            pdf.multi_cell(0, 6, line, fill=True)
        elif line.startswith("-") or line.startswith("*"):
            pdf.set_font("Arial", size=9)
            pdf.set_text_color(*DARK_GRAY)
            pdf.set_x(14)
            pdf.multi_cell(0, 5.5, "  " + line.lstrip("-* ").strip())
        else:
            pdf.set_font("Arial", size=9)
            pdf.set_text_color(*DARK_GRAY)
            pdf.multi_cell(0, 5.5, line)

    # Consultation transcript
    pdf.ln(5)
    pdf.set_fill_color(*MID_BLUE)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Arial", "B", 10)
    pdf.cell(0, 7, "   CONSULTATION TRANSCRIPT", ln=True, fill=True)
    pdf.ln(3)

    for msg in conversation:
        role    = msg.get("role", "user")
        content = clean_for_pdf(msg.get("content", ""))
        if not content.strip():
            continue
        if role == "user":
            pdf.set_font("Arial", "B", 8)
            pdf.set_text_color(*MID_BLUE)
            pdf.cell(0, 5, "You:", ln=True)
        else:
            pdf.set_font("Arial", "B", 8)
            pdf.set_text_color(*DARK_BLUE)
            pdf.cell(0, 5, "Vitalsy AI:", ln=True)
        pdf.set_font("Arial", size=8)
        pdf.set_text_color(*DARK_GRAY)
        pdf.multi_cell(0, 4.8, content)
        pdf.ln(2)
        pdf.set_draw_color(*LINE_GRAY)
        pdf.set_line_width(0.2)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(2)

    # Footer
    pdf.set_y(-30)
    pdf.set_fill_color(*DARK_BLUE)
    pdf.rect(0, pdf.get_y(), 210, 32, 'F')
    pdf.set_text_color(*WHITE)
    pdf.set_font("Arial", "B", 9)
    pdf.cell(0, 6, "Vitalsy Elite AI  |  Blood Report Analysis  |  vitalsy.ai", ln=True, align="C")
    pdf.set_font("Arial", "I", 7)
    pdf.cell(0, 5, "Disclaimer: This report is AI-generated for informational purposes only.", ln=True, align="C")
    pdf.cell(0, 4, "It does not constitute medical advice. Please consult a licensed healthcare professional.", ln=True, align="C")
    pdf.set_font("Arial", size=7)
    pdf.cell(0, 4, f"Generated: {now.strftime('%d %B %Y, %I:%M %p')}  |  Report ID: VEA-{now.strftime('%Y%m%d%H%M%S')}", ln=True, align="C")

    return pdf.output()


@app.get("/")
def root():
    return {"status": "Vitalsy Elite AI is running", "version": "4.0"}

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/chat")
async def chat(request: ChatRequest):
    reply = call_groq(request.message, request.history)
    save_chat(request.user_id, request.message, reply)
    return {
        "reply": reply,
        # Frontend appends these two entries to its history array before next call
        "new_history_entry": [
            {"role": "user",      "content": request.message},
            {"role": "assistant", "content": reply},
        ]
    }

@app.post("/analyze")
async def analyze(
    file:    UploadFile = File(...),
    user_id: str        = Form("guest"),
    history: str        = Form("[]"),  # JSON array of full conversation so far
):
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()

    # Parse prior conversation history from frontend
    try:
        prior_history = json.loads(history)
    except Exception:
        prior_history = []

    if ext in BLOCKED_EXTS:
        return {"analysis": "I am sorry, I cannot process audio or video files. Please upload your blood report as a PDF, Word document, Excel sheet, or image.", "filename": filename}

    if ext and ext not in SUPPORTED_EXTS:
        return {"analysis": f"I am not able to read .{ext.lstrip('.')} files right now. Please upload as PDF, DOCX, XLSX, CSV, TXT, or an image (JPG/PNG).", "filename": filename}

    try:
        file_bytes = await file.read()
        text       = extract_text(file_bytes, filename)

        if text == "[IMAGE_FILE]":
            analysis = analyse_image_with_groq(file_bytes, filename)
            save_chat(user_id, f"[Uploaded image: {filename}]", analysis)
            return {
                "analysis": analysis,
                "filename": filename,
                "new_history_entry": [
                    {"role": "user",      "content": f"[Uploaded blood report image: {filename}]"},
                    {"role": "assistant", "content": analysis},
                ]
            }

        if not text.strip():
            reply = call_groq(
                "The user uploaded a file but no text could be extracted. "
                "Apologise kindly and ask them to try a PDF or DOCX version or type the values in the chat.",
                prior_history
            )
            return {"analysis": reply, "filename": filename}

        prompt = (
            f"The user has uploaded a blood test report named '{filename}'. Extracted content:\n\n"
            f"{text[:5000]}\n\n"
            f"Analyse this fully as a blood report specialist following your complete analysis format. "
            f"You MUST include all four sections: parameter analysis, overall summary, what to do next, "
            f"food and nutrition recommendations (with specific foods to eat and avoid based on the results), "
            f"and lifestyle tips. If this is NOT a blood report (MRI, prescription etc.), "
            f"tell the user kindly you only handle blood reports right now."
        )
        analysis = call_groq(prompt, prior_history)
        save_chat(user_id, f"[Uploaded: {filename}]", analysis)
        return {
            "analysis": analysis,
            "filename": filename,
            "new_history_entry": [
                {"role": "user",      "content": f"[Uploaded blood report: {filename}]\n\n{text[:3000]}"},
                {"role": "assistant", "content": analysis},
            ]
        }

    except Exception as e:
        traceback.print_exc()
        return {"analysis": f"Something went wrong reading your file. Please try again. ({e})"}

class AnalyzeTextRequest(BaseModel):
    text:    str
    user_id: str  = "guest"
    history: list = []  # full conversation history

@app.post("/analyze-text")
async def analyze_text(request: AnalyzeTextRequest):
    if not request.text.strip():
        raise HTTPException(400, "No text provided")
    prompt = (
        f"The user has pasted their blood report values:\n\n{request.text[:5000]}\n\n"
        f"Analyse this fully as a blood report specialist following your complete analysis format. "
        f"You MUST include all four sections: parameter analysis, overall summary, what to do next, "
        f"food and nutrition recommendations (with specific foods to eat and avoid), and lifestyle tips."
    )
    analysis = call_groq(prompt, request.history)
    save_chat(request.user_id, f"[Pasted report]\n\n{request.text[:2000]}", analysis)
    return {
        "analysis": analysis,
        "new_history_entry": [
            {"role": "user",      "content": f"[Pasted blood report values]\n\n{request.text[:3000]}"},
            {"role": "assistant", "content": analysis},
        ]
    }

@app.get("/history/{user_id}")
async def get_history(user_id: str):
    if not db or user_id == "guest":
        return {"messages": []}
    try:
        ref  = db.collection("chats").document(user_id).collection("messages")
        docs = ref.order_by("timestamp").stream()
        return {"messages": [{"role": d.get("role"), "content": d.get("content")} for d in docs]}
    except Exception as e:
        return {"messages": [], "error": str(e)}

@app.delete("/history/{user_id}")
async def clear_history(user_id: str):
    if not db or user_id == "guest":
        return {"cleared": False}
    try:
        ref = db.collection("chats").document(user_id).collection("messages")
        for d in ref.stream(): d.reference.delete()
        return {"cleared": True}
    except Exception as e:
        return {"cleared": False, "error": str(e)}

@app.post("/download-pdf")
async def download_pdf(request: PDFRequest):
    try:
        pdf_bytes = build_pdf(request.patient_name, request.conversation)
        buf = io.BytesIO(pdf_bytes)
        buf.seek(0)
        safe = re.sub(r'[^a-zA-Z0-9_\-]', '_', request.patient_name)
        fn   = f"Vitalsy_Report_{safe}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        return StreamingResponse(
            buf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{fn}"',
                "Content-Type": "application/pdf",
                "Access-Control-Expose-Headers": "Content-Disposition",
            }
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"PDF generation failed: {e}")

@app.post("/download-pdf-form")
async def download_pdf_form(name: str = Form("Patient"), conversation: str = Form("[]")):
    try:    convo = json.loads(conversation)
    except: convo = []
    try:
        pdf_bytes = build_pdf(name, convo)
        buf = io.BytesIO(pdf_bytes)
        buf.seek(0)
        safe = re.sub(r'[^a-zA-Z0-9_\-]', '_', name)
        fn   = f"Vitalsy_Report_{safe}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        return StreamingResponse(
            buf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{fn}"',
                "Content-Type": "application/pdf",
                "Access-Control-Expose-Headers": "Content-Disposition",
            }
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"PDF generation failed: {e}")

if __name__ == "__main__":
    import uvicorn
    # Local dev: port 8000 with reload
    # On Render: use start command: uvicorn brain:app --host 0.0.0.0 --port 10000
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("brain:app", host="0.0.0.0", port=port, reload=False)