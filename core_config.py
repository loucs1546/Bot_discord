# core_config.py
import os
from typing import Dict, Any

CONFIG: Dict[str, Any] = {
    "security": {
        "anti_spam": False,
        "anti_hack": False,
        "anti_raid": False
    },
    "logs": {
        "messages": None,
        "vocal": None,
        "commands": None,
        "moderation": None,
        "profile": None,
        "content": None,
        "alerts": None,
        "sanctions": None,
        "ticket": None,
        "giveaway": None,
        "securite": None,
        "bavures": None,
        "bavures-sanctions": None
    },
    "ticket_config": {
        "mode": "basic",  # "basic" ou "advanced"
        "options": []  # List[str] - options de tickets personnalisées en mode advanced
    },
    "roles": {
        "default": None,  # Rôle de base à l'arrivée
        "admin": None,
        "moderator": None,
        "founder": None
    },
    "rules": ""
}
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))