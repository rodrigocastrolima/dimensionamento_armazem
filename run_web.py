"""Lança o servidor web do simulador a partir do diretório correto -- os
caminhos por defeito em Config (dados/...) são relativos a esta pasta."""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import uvicorn

uvicorn.run("warehouse.web:app", host="127.0.0.1", port=8000, reload=False)
