# utils/config_manager.py
import discord
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

CONFIG_FILENAME = ".seiko_config.json"
BACKUP_FOLDER = Path("backups")

# Créer le dossier de backups s'il n'existe pas
BACKUP_FOLDER.mkdir(exist_ok=True)


async def save_guild_config(guild: discord.Guild, config: Dict[str, Any]) -> Optional[discord.Message]:
    """
    Sauvegarde la configuration du serveur dans un fichier et l'upload dans le salon 'Sauvegarde'
    Retourne le message contenant le fichier, ou None en cas d'erreur
    """
    try:
        # Préparer les données
        backup_data = {
            "guild_id": guild.id,
            "guild_name": guild.name,
            "timestamp": datetime.utcnow().isoformat(),
            "config": config
        }

        # Créer le fichier JSON
        backup_file = BACKUP_FOLDER / f"{guild.id}_config.json"
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)

        # Chercher le salon "Sauvegarde"
        save_channel = None
        for channel in guild.text_channels:
            if channel.name == "sauvegarde" and isinstance(channel, discord.TextChannel):
                save_channel = channel
                break

        if not save_channel:
            return None

        # Uploader le fichier
        embed = discord.Embed(
            title="💾 Sauvegarde de Configuration",
            description=f"Configuration du serveur **{guild.name}**\nSauvegarde du: <t:{int(datetime.utcnow().timestamp())}:R>",
            color=0x2ecc71
        )
        embed.add_field(name="⚠️ IMPORTANT", value="**Ne supprimez pas ce fichier !**\nLe bot en a besoin pour reconfigurer le serveur automatiquement au redémarrage.", inline=False)
        embed.set_footer(text=f"Guild ID: {guild.id}")

        message = await save_channel.send(embed=embed, file=discord.File(backup_file, filename=CONFIG_FILENAME))
        return message

    except Exception as e:
        print(f"[ConfigManager] Erreur lors de la sauvegarde pour {guild.id}: {e}")
        return None


async def load_guild_config_from_file(guild: discord.Guild) -> Optional[Dict[str, Any]]:
    """
    Cherche et charge la configuration du serveur depuis le fichier dans le salon 'Sauvegarde'
    Retourne la config ou None si non trouvée
    """
    try:
        # Chercher le salon "Sauvegarde"
        save_channel = None
        for channel in guild.text_channels:
            if channel.name == "sauvegarde" and isinstance(channel, discord.TextChannel):
                save_channel = channel
                break

        if not save_channel:
            return None

        # Chercher le fichier de config
        async for message in save_channel.history(limit=100):
            for attachment in message.attachments:
                if attachment.filename == CONFIG_FILENAME:
                    # Télécharger et lire le fichier
                    await attachment.save(BACKUP_FOLDER / f"temp_{guild.id}.json")
                    with open(BACKUP_FOLDER / f"temp_{guild.id}.json", 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    # Nettoyer le fichier temporaire
                    os.remove(BACKUP_FOLDER / f"temp_{guild.id}.json")
                    return data.get("config")

        return None

    except Exception as e:
        print(f"[ConfigManager] Erreur lors du chargement pour {guild.id}: {e}")
        return None


async def create_backup_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """
    Crée le salon privé 'Sauvegarde' avec les permissions appropriées
    Retourne le canal créé ou None en cas d'erreur
    """
    try:
        # Vérifier si le salon existe déjà
        for channel in guild.text_channels:
            if channel.name == "sauvegarde":
                return channel

        # Créer le rôle everyone avec pas d'accès
        everyone_overwrite = discord.PermissionOverwrite(read_messages=False, send_messages=False)

        # Créer les rôles avec accès
        admin_overwrite = discord.PermissionOverwrite(read_messages=True, send_messages=False, manage_messages=False)

        # Créer le salon
        channel = await guild.create_text_channel(
            "📁-sauvegarde",
            topic="Fichiers de sauvegarde de la configuration Seiko - NE PAS SUPPRIMER",
            overwrites={
                guild.default_role: everyone_overwrite
            }
        )

        # Donner l'accès aux rôles admin/modérateurs
        admin_roles = []
        
        # Chercher les rôles stockés en config
        from core_config import CONFIG
        
        if "roles" in CONFIG:
            if CONFIG["roles"].get("admin"):
                admin_role = guild.get_role(CONFIG["roles"]["admin"])
                if admin_role:
                    admin_roles.append(admin_role)
            if CONFIG["roles"].get("moderator"):
                mod_role = guild.get_role(CONFIG["roles"]["moderator"])
                if mod_role:
                    admin_roles.append(mod_role)

        for role in admin_roles:
            await channel.set_permissions(role, read_messages=True, send_messages=False)

        return channel

    except Exception as e:
        print(f"[ConfigManager] Erreur lors de la création du salon Sauvegarde: {e}")
        return None


async def send_missing_config_alert(guild: discord.Guild):
    """
    Envoie une alerte dans le premier salon où le bot peut écrire
    pour signaler que la config n'a pas été trouvée
    """
    try:
        # Chercher un salon pour envoyer le message
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                embed = discord.Embed(
                    title="⚠️ Configuration Manquante",
                    description="Le fichier de sauvegarde de configuration n'a pas été trouvé.",
                    color=0xe74c3c
                )
                embed.add_field(
                    name="Que faire ?",
                    value="Utilisez `/start` pour reconfigurer le serveur.\n"
                          "Un nouveau fichier de sauvegarde sera créé dans le salon **📁-sauvegarde**.",
                    inline=False
                )
                await channel.send(embed=embed)
                break

    except Exception as e:
        print(f"[ConfigManager] Erreur lors de l'envoi de l'alerte: {e}")
