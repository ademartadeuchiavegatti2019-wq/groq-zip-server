from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import zipfile
import io
import requests

app = FastAPI(title="GroqStudio Cloud Middleware")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def health_check():
    return {"status": "online", "service": "GroqStudio Zip/APK Cloud Processor"}

@app.post("/analyze-archive")
async def analyze_archive(
    file: UploadFile = File(...),
    prompt: str = Form("Analise a estrutura e os códigos extraídos deste pacote."),
    api_key: str = Form(...),
    model: str = Form("llama-3.3-70b-versatile")
):
    try:
        contents = await file.read()
        zip_buffer = io.BytesIO(contents)
        
        if not zipfile.is_zipfile(zip_buffer):
            raise HTTPException(status_code=400, detail="O arquivo enviado não é um ZIP ou APK válido.")

        file_tree = []
        extracted_blocks = []
        total_chars = 0
        # 22.000 caracteres (~5.500 tokens) cabe com folga no limite gratuito de 8.000 TPM da Groq
        MAX_CHARS = 22000

        valid_extensions = (
            '.java', '.kt', '.js', '.jsx', '.ts', '.tsx', '.py',
            '.json', '.xml', '.gradle', '.properties'
        )

        with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
            all_names = zip_ref.namelist()
            file_tree = [f for f in all_names if not f.endswith('/')]

            # Ordena para ler primeiro AndroidManifest, build.gradle e códigos-fonte principais
            prioritized_files = sorted(
                file_tree,
                key=lambda x: (
                    0 if 'AndroidManifest' in x else
                    1 if 'build.gradle' in x else
                    2 if x.endswith(('.java', '.kt', '.js', '.ts')) else 3
                )
            )

            for fname in prioritized_files:
                if total_chars >= MAX_CHARS:
                    break
                if fname.lower().endswith(valid_extensions) or 'AndroidManifest' in fname:
                    try:
                        with zip_ref.open(fname) as f:
                            raw_bytes = f.read()
                            text_content = raw_bytes.decode('utf-8', errors='ignore').strip()
                            if text_content:
                                available = MAX_CHARS - total_chars
                                snippet = text_content[:min(available, 4500)]
                                block = f"\n--- 📄 {fname} ---\n{snippet}\n"
                                extracted_blocks.append(block)
                                total_chars += len(block)
                    except Exception:
                        continue

        tree_summary = "\n".join([f"  - {f}" for f in file_tree[:40]])
        if len(file_tree) > 40:
            tree_summary += f"\n  ... (+{len(file_tree) - 40} arquivos)"

        final_context = (
            f"📦 PACOTE: {file.filename} ({len(file_tree)} arquivos internos)\n"
            f"📄 Lidos: {len(extracted_blocks)} arquivos ({round(total_chars/1024, 1)} KB)\n\n"
            f"--- ESTRUTURA ---\n{tree_summary}\n\n"
            f"--- CÓDIGOS PRINCIPAIS ---\n"
            + "".join(extracted_blocks)
        )

        groq_res = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": f"{prompt}\n\n{final_context}"
                    }
                ]
            },
            timeout=120
        )

        data = groq_res.json()
        if "error" in data:
            raise HTTPException(status_code=400, detail=data["error"].get("message", "Erro na API Groq"))

        return {
            "reply": data["choices"][0]["message"]["content"],
            "stats": {
                "total_files": len(file_tree),
                "extracted_files": len(extracted_blocks),
                "chars_sent": total_chars
            }
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha interna ao processar arquivo: {str(e)}")
