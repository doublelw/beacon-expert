FROM freecad/freecad:latest

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir fastapi uvicorn sqlalchemy python-jose passlib bcrypt python-multipart httpx ezdxf

EXPOSE 8767

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8767"]
