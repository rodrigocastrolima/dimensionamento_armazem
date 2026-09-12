"""Lança o servidor web do simulador a partir do diretório correto -- os
caminhos por defeito em Config (dados/...) são relativos a esta pasta.

HOST/PORT lidos de $HOST/$PORT (convenção da maioria das plataformas de
deployment -- ver Dockerfile/DEPLOYMENT.md) com os defaults de sempre
(127.0.0.1:8000) quando não definidas, para o uso local continuar
idêntico. WAREHOUSE_CLIENT/WAREHOUSE_MASTER_DATA continuam a ser lidas
por warehouse/web.py, não aqui."""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import uvicorn

host = os.environ.get("HOST", "127.0.0.1")
port = int(os.environ.get("PORT", "8000"))
uvicorn.run("warehouse.web:app", host=host, port=port, reload=False)
