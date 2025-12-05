FROM python:3.13-slim

WORKDIR /app

# Installer audioop-lts pour Python 3.13
RUN pip install --no-cache-dir audioop-lts

# Copier les requirements
COPY requirements.txt .

# Installer les dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code de l'application
COPY . .

# Démarrer le bot
CMD ["python", "main.py"]
