from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import investigations

app = FastAPI(title="AI Log Analysis System - Orchestrator API")

# Configure CORS for local Vite development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(investigations.router, prefix="/api")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
