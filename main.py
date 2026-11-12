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
        MAX_CHARS = 95000  # Limite seguro de contexto para os modelos da Groq

        valid_extensions = (
            '.java', '.kt', '.js', '.jsx', '.ts', '.tsx', '.py',
            '.json', '.xml', '.gradle', '.txt', '.md', '.properties', '.smali'
        )

        with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
            all_names = zip_ref.namelist()
            file_tree = [f for f in all_names if not f.endswith('/')]

            for fname in file_tree:
                if total_chars >= MAX_CHARS:
                    break
                if fname.lower().endswith(valid_extensions) or 'AndroidManifest' in fname:
                    try:
                        with zip_ref.open(fname) as f:
                            raw_bytes = f.read()
                            text_content = raw_bytes.decode('utf-8', errors='ignore').strip()
                            if text_content:
                                available = MAX_CHARS - total_chars
                                snippet = text_content[:available]
                                block = f"\n=========================================\n📄 ARQUIVO: {fname}\n=========================================\n{snippet}\n"
                                extracted_blocks.append(block)
                                total_chars += len(block)
                    except Exception:
                        continue

        tree_summary = "\n".join([f"  - {f}" for f in file_tree[:60]])
        if len(file_tree) > 60:
            tree_summary += f"\n  ... (+{len(file_tree) - 60} arquivos ocultos na listagem)"

        final_context = (
            f"📦 PACOTE PROCESSADO NA NUVEM: {file.filename}\n"
            f"📁 Total de arquivos internos: {len(file_tree)}\n"
            f"📄 Arquivos de código lidos: {len(extracted_blocks)} ({round(total_chars/1024, 1)} KB)\n\n"
            f"--- ÁRVORE DE DIRETÓRIOS ---\n{tree_summary}\n\n"
            f"--- CONTEÚDO DOS ARQUIVOS EXTRAÍDOS ---\n"
            + "".join(extracted_blocks)
        )

        # Enviar para a API da Groq usando a sua chave
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
