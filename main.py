# main.py - VERSION CONSOLIDÉE AVEC TOUTES LES COMMANDES
import discord
from discord.ext import commands
import core_config as config
import asyncio
import aiohttp
from asyncio import CancelledError
from flask import Flask
from threading import Thread
from datetime import datetime
from pathlib import Path
import re
import io
import requests
from utils.logging import send_log_to
from utils.config_manager import save_guild_config, load_guild_config_from_file, create_backup_channel, send_missing_config_alert
import utils.config_manager as config_manager

# === MINI SERVEUR WEB POUR RENDRE/KEEP ALIVE ===
import os
import time

app = Flask("")

# Variables globales pour le keep-alive amélioré
last_activity = time.time()
activity_counter = 0

@app.route("/")
def home():
    global last_activity
    last_activity = time.time()
    return "Bot Seiko Security en ligne ! 🚀"

@app.route("/ping")
def ping():
    global last_activity
    last_activity = time.time()
    return {"status": "ok", "timestamp": time.time()}

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# Lance le serveur Flask dans un thread séparé
t = Thread(target=run, daemon=True)
t.start()

# === CONFIGURATION DU BOT DISCORD ===
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
cogs_loaded = False

# === HELPERS ===
def est_bavure_raison(raison: str) -> bool:
    if not raison or raison.strip().lower() in ("", "aucune raison"):
        return True
    mots = re.findall(r'\b[a-zA-Z]{2,}\b', raison)
    if len(mots) < 2:
        return True
    voyelles = "aeiouy"
    valid_count = 0
    for mot in mots:
        if any(c.lower() in voyelles for c in mot):
            valid_count += 1
            if valid_count >= 2:
                return False
    return True

def get_sanction_channel(bot):
    return bot.get_channel(config.CONFIG["logs"].get("sanctions"))

# === SELECT MENUS POUR CONFIG ===
class RoleSelect(discord.ui.Select):
    def __init__(self, role_type: str):
        self.role_type = role_type
        super().__init__(
            placeholder=f"Sélectionner le rôle {role_type}...",
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        # Les options sont remplies dynamiquement dans la view
        role_id = int(self.values[0])
        config.CONFIG.setdefault("roles", {})[self.role_type] = role_id
        await interaction.response.send_message(f"✅ Rôle {self.role_type} défini : <@&{role_id}>", ephemeral=True)


class ChannelSelect(discord.ui.Select):
    def __init__(self, channel_type: str):
        self.channel_type = channel_type
        super().__init__(
            placeholder=f"Sélectionner le salon {channel_type}...",
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        channel_id = int(self.values[0])
        config.CONFIG.setdefault("channels", {})[self.channel_type] = channel_id
        await interaction.response.send_message(f"✅ Salon {self.channel_type} défini : <#{channel_id}>", ephemeral=True)


class LogChannelSelect(discord.ui.Select):
    def __init__(self, log_type: str):
        self.log_type = log_type
        super().__init__(
            placeholder=f"Sélectionner salon pour logs {log_type}...",
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        channel_id = int(self.values[0])
        config.CONFIG.setdefault("logs", {})[self.log_type] = channel_id
        await interaction.response.send_message(f"✅ Logs {self.log_type} → <#{channel_id}>", ephemeral=True)


# === VIEWS AVEC SELECT MENUS ===
class RoleSelectView(discord.ui.View):
    def __init__(self, guild: discord.Guild, role_type: str, next_view_factory: callable = None, back_view_factory: callable = None):
        """next_view_factory: optional callable taking guild and returning a discord.ui.View to show after selection"""
        super().__init__(timeout=600)
        self.guild = guild
        self.role_type = role_type
        self.next_view_factory = next_view_factory
        select = RoleSelect(role_type)
        select.options = [
            discord.SelectOption(label=role.name, value=str(role.id))
            for role in guild.roles
            if role.name != "@everyone"
        ][:25]  # Max 25 options

        async def select_callback(interaction: discord.Interaction):
            role_id = int(select.values[0])
            config.CONFIG.setdefault("roles", {})[self.role_type] = role_id
            # Si une factory est fournie, éditer le message et afficher la vue suivante
            if self.next_view_factory:
                try:
                    await interaction.response.edit_message(content=None, embed=None, view=self.next_view_factory(self.guild))
                except Exception:
                    await interaction.response.send_message(f"✅ Rôle {self.role_type} défini : <@&{role_id}>", ephemeral=True)
            else:
                await interaction.response.send_message(f"✅ Rôle {self.role_type} défini : <@&{role_id}>", ephemeral=True)

        select.callback = select_callback
        self.add_item(select)

        # Bouton retour - utilise back_view_factory si fourni, sinon RolesSalonsView
        back_factory = back_view_factory or (lambda g: RolesSalonsView(g))

        class BackButton(discord.ui.Button):
            def __init__(self):
                super().__init__(label="⬅️ Retour", style=discord.ButtonStyle.secondary)

            async def callback(self, interaction: discord.Interaction):
                try:
                    await interaction.response.edit_message(embed=None, view=back_factory(guild))
                except Exception:
                    await interaction.response.send_message("🔙 Retour", ephemeral=True)

        self.add_item(BackButton())


class ChannelSelectView(discord.ui.View):
    def __init__(self, guild: discord.Guild, channel_type: str, next_view_factory: callable = None, back_view_factory: callable = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.channel_type = channel_type
        self.next_view_factory = next_view_factory
        select = ChannelSelect(channel_type)
        select.options = [
            discord.SelectOption(label=channel.name, value=str(channel.id))
            for channel in guild.text_channels
        ][:25]

        async def select_callback(interaction: discord.Interaction):
            chan_id = int(select.values[0])
            config.CONFIG.setdefault("channels", {})[self.channel_type] = chan_id
            if self.next_view_factory:
                try:
                    await interaction.response.edit_message(content=None, embed=None, view=self.next_view_factory(self.guild))
                except Exception:
                    await interaction.response.send_message(f"✅ Salon {self.channel_type} défini : <#{chan_id}>", ephemeral=True)
            else:
                await interaction.response.send_message(f"✅ Salon {self.channel_type} défini : <#{chan_id}>", ephemeral=True)

        select.callback = select_callback
        self.add_item(select)

        # Back button
        back_factory = back_view_factory or (lambda g: RolesSalonsView(g))

        class BackButton(discord.ui.Button):
            def __init__(self):
                super().__init__(label="⬅️ Retour", style=discord.ButtonStyle.secondary)

            async def callback(self, interaction: discord.Interaction):
                try:
                    await interaction.response.edit_message(embed=None, view=back_factory(guild))
                except Exception:
                    await interaction.response.send_message("🔙 Retour", ephemeral=True)

        self.add_item(BackButton())


class LogChannelSelectView(discord.ui.View):
    def __init__(self, guild: discord.Guild, log_type: str):
        super().__init__(timeout=600)
        select = LogChannelSelect(log_type)
        select.options = [
            discord.SelectOption(label=channel.name, value=str(channel.id)) 
            for channel in guild.text_channels
        ][:25]
        self.add_item(select)

# === VIEWS POUR TICKETS ===
class TicketChoiceSelect(discord.ui.Select):
    """Select menu pour choisir le type de ticket"""
    def __init__(self, guild: discord.Guild):
        # Récupérer les options depuis CONFIG ou utiliser les defaults
        options_list = config.CONFIG.get("ticket_config", {}).get("options", [])
        if not options_list:
            options_list = ["Support Général", "Bug Report", "Suggestion", "Autre"]
        
        select_options = [
            discord.SelectOption(label=opt[:100], value=opt) 
            for opt in options_list[:25]  # Limit Discord 25 options
        ]
        
        super().__init__(
            placeholder="Sélectionner le type de ticket...",
            options=select_options,
            min_values=1,
            max_values=1
        )
        self.guild = guild
    
    async def callback(self, interaction: discord.Interaction):
        # On va stocker le choix et afficher le bouton "Créer"
        pass


class TicketChoiceView(discord.ui.View):
    """Interface pour choisir le type de ticket avant création"""
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        self.guild = guild
        self.selected_option = None
        
        # Ajouter le Select menu
        select = TicketChoiceSelect(guild)
        
        # Override le callback pour stocker la sélection
        async def select_callback(interaction: discord.Interaction):
            self.selected_option = select.values[0]
            await interaction.response.defer()
        
        select.callback = select_callback
        self.add_item(select)
    
    @discord.ui.button(label="📩 Créer le Ticket", style=discord.ButtonStyle.success)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_option:
            await interaction.response.send_message("❌ Sélectionnez un type de ticket d'abord.", ephemeral=True)
            return
        
        guild = self.guild or interaction.guild
        user = interaction.user
        
        # Vérifier qu'il n'a pas déjà un ticket
        for channel in guild.channels:
            if channel.name.startswith("ticket-") and str(user.id) in channel.name:
                await interaction.response.send_message("Vous avez déjà un ticket ouvert !", ephemeral=True)
                return
        
        # Incrémenter le counter
        config.CONFIG.setdefault("ticket_config", {}).setdefault("counter", 0)
        ticket_num = config.CONFIG["ticket_config"]["counter"] + 1
        config.CONFIG["ticket_config"]["counter"] = ticket_num
        
        # Créer le channel ticket-XXXXXX
        ticket_name = f"ticket-{str(ticket_num).zfill(6)}"
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(
                read_messages=True, 
                send_messages=True, 
                attach_files=False, 
                embed_links=False
            ),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        }
        
        try:
            ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                overwrites=overwrites,
                reason=f"Ticket créé par {user} ({self.selected_option})"
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur création ticket: {e}", ephemeral=True)
            return
        
        # Envoyer le message du bot avec les boutons de gestion
        embed = discord.Embed(
            title=f"🎟️ {self.selected_option} - #{ticket_num:06d}",
            description=f"Bonjour {user.mention},\n\n📝 Décrivez votre demande en détail. Un membre de l'équipe vous répondra bientôt.\n\n> ⚠️ Les fichiers et liens ne sont pas autorisés dans les tickets.",
            color=0x5865F2,
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="Seiko Security • Système de tickets")
        
        view = TicketManagementView(user.id, ticket_num)
        msg = await ticket_channel.send(embed=embed, view=view)
        
        # Log
        log_embed = discord.Embed(
            title="🎟️ Ticket créé",
            description=f"**Utilisateur** : {user.mention} (`{user}`)\n**Type** : {self.selected_option}\n**Ticket** : {ticket_channel.mention}",
            color=0x00ff00,
            timestamp=datetime.utcnow()
        )
        log_embed.set_thumbnail(url=user.display_avatar.url)
        await send_log_to(bot, "ticket", log_embed)
        
        await interaction.response.send_message(
            f"✅ Ticket créé: {ticket_channel.mention}\n💬 Type: **{self.selected_option}**",
            ephemeral=True
        )


class TicketManagementView(discord.ui.View):
    """Boutons de gestion du ticket (Claim, Close, Reopen, Delete)"""
    def __init__(self, owner_id: int, ticket_num: int = None):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.ticket_num = ticket_num
        self.is_closed = False
    
    @discord.ui.button(label="👤 Claim", style=discord.ButtonStyle.primary, emoji="✋", custom_id="ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Claim = Clear tous les messages sauf le premier du bot"""
        if not any(role.permissions.administrator or role.permissions.manage_messages for role in interaction.user.roles):
            await interaction.response.send_message("❌ Permissions insuffisantes.", ephemeral=True)
            return
        
        await interaction.response.defer()
        channel = interaction.channel
        
        # Récupérer tous les messages
        messages_to_delete = []
        first = True
        async for msg in channel.history(limit=None, oldest_first=True):
            if first and msg.author == interaction.client.user:
                first = False
                continue
            messages_to_delete.append(msg)
        
        # Supprimer (par batch pour éviter rate limit)
        deleted_count = 0
        for msg in messages_to_delete:
            try:
                await msg.delete()
                deleted_count += 1
            except:
                pass
        
        embed = discord.Embed(
            title="✅ Ticket Claimed",
            description=f"Par {interaction.user.mention}\n🗑️ {deleted_count} messages supprimés",
            color=0x2ecc71
        )
        await interaction.followup.send(embed=embed)
    @discord.ui.button(label="🛠️ Prendre en charge", style=discord.ButtonStyle.secondary, emoji="🛠️", custom_id="ticket_take")
    async def take_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Prendre en charge = signale que le staff prend en charge la demande"""
        # Autorisé seulement pour fondateur/admin/modérateur
        allowed = False
        # rôles définis dans la config
        role_ids = [config.CONFIG.get("roles", {}).get(k) for k in ("founder", "admin", "moderator")]
        role_ids = [rid for rid in role_ids if rid]
        for role in interaction.user.roles:
            if role.id in role_ids or role.permissions.administrator or role.permissions.manage_messages:
                allowed = True
                break
        if not allowed:
            await interaction.response.send_message("❌ Permissions insuffisantes.", ephemeral=True)
            return

        await interaction.response.defer()
        # Envoyer un message public dans le ticket
        try:
            await interaction.channel.send(f"✅ {interaction.user.mention} prend en charge votre demande.")
            await interaction.followup.send("✅ Vous avez pris en charge le ticket.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erreur: {e}", ephemeral=True)
    
    @discord.ui.button(label="🗑️ Supprimer", style=discord.ButtonStyle.danger, emoji="❌", custom_id="ticket_delete")
    async def delete_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Delete = Supprimer le canal avec confirmation"""
        if not any(role.permissions.administrator or role.permissions.manage_messages for role in interaction.user.roles):
            await interaction.response.send_message("❌ Permissions insuffisantes.", ephemeral=True)
            return
        
        # Confirmation
        embed = discord.Embed(
            title="⚠️ Confirmer la suppression",
            description="Ce ticket va être supprimé **définitivement** dans 5 secondes.\nCliquez sur ✅ pour confirmer ou ❌ pour annuler.",
            color=0xe74c3c
        )
        
        class ConfirmDeleteView(discord.ui.View):
            def __init__(self, ticket_channel, owner_id=None, ticket_num=None):
                super().__init__(timeout=5)
                self.ticket_channel = ticket_channel
                self.owner_id = owner_id
                self.ticket_num = ticket_num
            
            @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.danger, emoji="✅")
            async def confirm_delete(self, confirm_interaction: discord.Interaction, confirm_button: discord.ui.Button):
                await confirm_interaction.response.defer()
                # Envoyer un récap/log avant suppression
                try:
                    owner = None
                    owner_id = getattr(self, 'owner_id', None)
                    if owner_id:
                        owner = confirm_interaction.guild.get_member(owner_id)

                    embed_del = discord.Embed(
                        title="🗑️ Ticket supprimé",
                        description=f"**Salon**: {self.ticket_channel.name}\n**Supprimé par**: {confirm_interaction.user.mention}",
                        color=0xe74c3c,
                        timestamp=datetime.utcnow()
                    )
                    if owner:
                        embed_del.add_field(name="Propriétaire", value=f"{owner.mention} (`{owner}`)")
                    if getattr(self, 'ticket_num', None):
                        embed_del.set_footer(text=f"Ticket #{int(self.ticket_num):06d}")

                    await send_log_to(bot, "ticket", embed_del)
                except Exception:
                    pass

                try:
                    await self.ticket_channel.delete()
                except Exception:
                    pass
            
            @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, emoji="❌")
            async def cancel_delete(self, cancel_interaction: discord.Interaction, cancel_button: discord.ui.Button):
                await cancel_interaction.response.send_message("❌ Suppression annulée.", ephemeral=True)
        
        await interaction.response.send_message(embed=embed, view=ConfirmDeleteView(interaction.channel, owner_id=self.owner_id, ticket_num=self.ticket_num), ephemeral=True)


# TicketView REFACTORISÉE - Utilise TicketChoiceView
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Créer un ticket", style=discord.ButtonStyle.success, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user
        
        # Vérifier qu'il n'a pas déjà un ticket
        for channel in guild.channels:
            if channel.name.startswith("ticket-") and str(user.id) in channel.name:
                await interaction.response.send_message("Vous avez déjà un ticket ouvert !", ephemeral=True)
                return

        # Si le mode est 'basic', créer directement le ticket sans choix
        mode = config.CONFIG.get("ticket_config", {}).get("mode", "basic")
        options = config.CONFIG.get("ticket_config", {}).get("options", [])
        if mode == "basic":
            selected_option = options[0] if options else "Support Général"

            # Incrémenter le counter
            config.CONFIG.setdefault("ticket_config", {}).setdefault("counter", 0)
            ticket_num = config.CONFIG["ticket_config"]["counter"] + 1
            config.CONFIG["ticket_config"]["counter"] = ticket_num

            # Créer le channel ticket-XXXXXX
            ticket_name = f"ticket-{str(ticket_num).zfill(6)}"
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    attach_files=False,
                    embed_links=False
                ),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
            }

            try:
                ticket_channel = await guild.create_text_channel(
                    name=ticket_name,
                    overwrites=overwrites,
                    reason=f"Ticket créé par {user} ({selected_option})"
                )
            except Exception as e:
                await interaction.response.send_message(f"❌ Erreur création ticket: {e}", ephemeral=True)
                return

            # Envoyer le message du bot avec les boutons de gestion
            embed = discord.Embed(
                title=f"🎟️ {selected_option} - #{ticket_num:06d}",
                description=f"Bonjour {user.mention},\n\n📝 Décrivez votre demande en détail. Un membre de l'équipe vous répondra bientôt.\n\n> ⚠️ Les fichiers et liens ne sont pas autorisés dans les tickets.",
                color=0x5865F2,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text="Seiko Security • Système de tickets")

            view = TicketManagementView(user.id, ticket_num)
            await ticket_channel.send(embed=embed, view=view)

            # Log
            log_embed = discord.Embed(
                title="🎟️ Ticket créé",
                description=f"**Utilisateur** : {user.mention} (`{user}`)\n**Type** : {selected_option}\n**Ticket** : {ticket_channel.mention}",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            log_embed.set_thumbnail(url=user.display_avatar.url)
            await send_log_to(bot, "ticket", log_embed)

            await interaction.response.send_message(
                f"✅ Ticket créé: {ticket_channel.mention}\n💬 Type: **{selected_option}**",
                ephemeral=True
            )
            return

        # Sinon afficher l'interface de choix
        embed = discord.Embed(
            title="🎟️ Créer un Ticket",
            description="Sélectionnez le type de ticket et cliquez sur 'Créer le Ticket'",
            color=0x5865F2
        )
        await interaction.response.send_message(embed=embed, view=TicketChoiceView(guild), ephemeral=True)


# TicketControls est maintenant un alias pour TicketManagementView (compatibilité)
class TicketControls(TicketManagementView):
    """Classe de compatibilité - anciennement gérait les tickets"""
    pass


# === EVENT: on_ready ===
@bot.event
async def on_ready():
    global cogs_loaded
    print(f"✅ {bot.user} est en ligne !")
    
    if not cogs_loaded:
        # Charger UNIQUEMENT les listeners (pas de commandes ici!)
        cog_paths = [
            "cogs.logging",
            "cogs.security.antiraid",
            "cogs.security.antispam",
            "cogs.security.content_filter",
            "cogs.security.link_filter",
        ]
        
        for cog in cog_paths:
            try:
                await bot.load_extension(cog)
                print(f"✅ Cog (listener) chargé : {cog}")
            except Exception as e:
                print(f"❌ Erreur chargement {cog} : {e}")

        # Attendre que les cogs soient chargés
        await asyncio.sleep(1)

        # SYNCHRONISER LES COMMANDES
        try:
            if config.GUILD_ID:
                guild = discord.Object(id=config.GUILD_ID)
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                print(f"✅ {len(synced)} commandes synchronisées !")
                print(f"📝 Commandes : {[c.name for c in synced]}")
            else:
                synced = await bot.tree.sync()
                print(f"✅ {len(synced)} commandes globales synchronisées")
        except Exception as e:
            print(f"❌ Erreur synchronisation : {e}")
        
        # ===== REMPLACÉ : plus de scan automatique des sauvegardes au démarrage =====
        print("\nℹ️ Le scan automatique des sauvegardes au démarrage a été désactivé.")
        print("ℹ️ Utilisez la commande /load-save <salon_de_sauvegarde> pour charger une configuration depuis un salon de sauvegarde.")
        
        cogs_loaded = True
        
        # AJOUTER LES VIEWS PERSISTANTES
        bot.add_view(TicketView())
        bot.add_view(TicketControls(0))
        print("✅ Views ticket enregistrées")
        
        # Démarrer la boucle de self-ping (anti-AFK via PING sur PUBLIC_URL)
        try:
            if not hasattr(bot, "self_ping_task") or bot.self_ping_task.done():
                bot.self_ping_task = asyncio.create_task(self_ping_loop())
                print("✅ Self-ping task démarrée")
        except Exception as e:
            print(f"❌ Impossible de démarrer self-ping task: {e}")


# === SYSTÈME ANTI-AFK ===
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://seiko-security.onrender.com/")
PING_INTERVAL = int(os.environ.get("PING_INTERVAL", 240))  # 240s = 4 minutes


async def self_ping_loop():
    await asyncio.sleep(10)  # laisse le bot démarrer proprement
    session = aiohttp.ClientSession()
    try:
        while True:
            try:
                async with session.get(PUBLIC_URL, timeout=10) as resp:
                    status = resp.status
                    try:
                        text = await resp.text()
                    except Exception:
                        text = ""
                    print(f"[SELF PING] {PUBLIC_URL} -> {status}")
            except Exception as e:
                print(f"[SELF PING] erreur: {e}")
            await asyncio.sleep(PING_INTERVAL)
    except CancelledError:
        pass
    finally:
        await session.close()



# ============================
# === COMMANDES GÉNÉRALES ===
# ============================

@bot.tree.command(name="ping", description="Affiche la latence du bot")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong ! Latence : **{latency} ms**", ephemeral=True)


# ============================
# === COMMANDES DE LOGS ===
# ============================

@bot.tree.command(name="logs", description="Définit le salon pour un type de log")
@discord.app_commands.describe(type="Type de log", salon="Salon de destination")
@discord.app_commands.choices(type=[
    discord.app_commands.Choice(name="messages", value="messages"),
    discord.app_commands.Choice(name="moderation", value="moderation"),
    discord.app_commands.Choice(name="ticket", value="ticket"),
    discord.app_commands.Choice(name="vocal", value="vocal"),
    discord.app_commands.Choice(name="securite", value="securite")
])
@discord.app_commands.checks.has_permissions(administrator=True)
async def logs_cmd(interaction: discord.Interaction, type: str, salon: discord.TextChannel):
    config.CONFIG.setdefault("logs", {})[type] = salon.id
    embed = discord.Embed(
        title="📌 Configuration des logs",
        description=f"Le type **{type}** sera envoyé dans {salon.mention}.",
        color=0x2f3136,
        timestamp=discord.utils.utcnow()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="scan-deleted", description="Récupère les suppressions récentes manquées")
@discord.app_commands.checks.has_permissions(administrator=True)
async def scan_deleted(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    count = 0
    async for entry in interaction.guild.audit_logs(action=discord.AuditLogAction.message_delete, limit=50):
        if (discord.utils.utcnow() - entry.created_at).total_seconds() > 300:
            break
        embed = discord.Embed(
            title="🗑️ Message supprimé (récupéré)",
            description=f"**Auteur** : {entry.target}\n**Supprimé par** : {entry.user}",
            color=0xff8800,
            timestamp=entry.created_at
        )
        await send_log_to(bot, "messages", embed)
        count += 1
    await interaction.followup.send(f"✅ {count} suppressions récupérées.", ephemeral=True)

@bot.tree.command(name="add-cat-log", description="Crée une catégorie complète de salons de surveillance")
@discord.app_commands.checks.has_permissions(administrator=True)
async def add_cat_log(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    for category in guild.categories:
        if "log" in category.name.lower() or "surveillance" in category.name.lower():
            await interaction.followup.send(
                f"❌ Une catégorie de logs existe déjà : **{category.name}**",
                ephemeral=True
            )
            return

    try:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        category = await guild.create_category(
            name="𓆩𖤍𓆪۰⟣ SURVEILLANCES ⟢۰𓆩𖤍𓆪",
            overwrites=overwrites
        )

        salon_configs = [
            ("📜・messages", "messages"),
            ("🎤・vocal", "vocal"),
            ("🎫・tickets", "ticket"),
            ("🛠️・commandes", "commands"),
            ("👑・rôles", "moderation"),
            ("📛・profil", "profile"),
            ("🔍・contenu", "content"),
            ("🚨・alertes", "alerts"),
            ("⚖️・sanctions", "sanctions"),
            ("🎉・giveaway", "giveaway"),
            ("💥・bavures", "bavures")
        ]

        channel_ids = {}
        for name, key in salon_configs:
            log_overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(
                    read_messages=True, 
                    send_messages=True,
                    manage_messages=True
                )
            }
            channel = await guild.create_text_channel(name=name, category=category, overwrites=log_overwrites)
            channel_ids[key] = channel.id

        if not isinstance(config.CONFIG, dict):
            config.CONFIG = {}
        config.CONFIG.setdefault("logs", {})
        config.CONFIG["logs"].update(channel_ids)

        await interaction.followup.send(
            f"✅ Catégorie **{category.name}** créée avec {len(salon_configs)} salons !",
            ephemeral=True
        )

    except Exception as e:
        await interaction.followup.send(f"❌ Erreur : {str(e)}", ephemeral=True)

@bot.tree.command(name="create-categorie", description="Crée une catégorie personnalisée")
@discord.app_commands.describe(nom="Nom de la catégorie")
@discord.app_commands.checks.has_permissions(administrator=True)
async def create_categorie(interaction: discord.Interaction, nom: str):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    
    try:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        category = await guild.create_category(name=nom, overwrites=overwrites)
        await interaction.followup.send(
            f"✅ Catégorie **{category.name}** créée avec succès !\nID : `{category.id}`",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Erreur : {str(e)}", ephemeral=True)

@bot.tree.command(name="create-salon", description="Crée un salon dans une catégorie")
@discord.app_commands.describe(
    nom="Nom du salon",
    categorie="Catégorie où créer le salon"
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def create_salon(interaction: discord.Interaction, nom: str, categorie: discord.CategoryChannel):
    await interaction.response.defer(ephemeral=True)
    
    try:
        channel = await categorie.create_text_channel(name=nom)
        await interaction.followup.send(
            f"✅ Salon **#{channel.name}** créé dans **{categorie.name}** !\nID : `{channel.id}`",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Erreur : {str(e)}", ephemeral=True)


# ============================
# === COMMANDES DE SALON ===
# ============================

@bot.tree.command(name="clear-salon", description="Supprime tous les messages du salon")
@discord.app_commands.checks.has_permissions(manage_messages=True)
async def clear_salon(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=1000)
    await interaction.followup.send(f"🧹 **{len(deleted)}** messages supprimés.", ephemeral=True)

@bot.tree.command(name="delete-salon", description="Supprime un salon")
@discord.app_commands.describe(salon="Salon à supprimer")
@discord.app_commands.checks.has_permissions(manage_channels=True)
async def delete_salon(interaction: discord.Interaction, salon: discord.TextChannel):
    await salon.delete(reason=f"Supprimé par {interaction.user}")
    await interaction.response.send_message(f"✅ Salon **{salon.name}** supprimé.", ephemeral=True)

@bot.tree.command(name="delete-categorie", description="Supprime une catégorie et ses salons")
@discord.app_commands.describe(categorie="Catégorie à supprimer")
@discord.app_commands.checks.has_permissions(manage_channels=True)
async def delete_categorie(interaction: discord.Interaction, categorie: discord.CategoryChannel):
    await interaction.response.send_message("✅ Suppression en cours...", ephemeral=True)
    for channel in categorie.channels:
        try:
            await channel.delete(reason=f"Supprimé avec la catégorie par {interaction.user}")
        except:
            pass
    try:
        await categorie.delete(reason=f"Supprimé par {interaction.user}")
    except:
        pass

@bot.tree.command(name="say", description="Envoie un message dans un salon")
@discord.app_commands.describe(salon="Salon cible", contenu="Message à envoyer")
@discord.app_commands.checks.has_permissions(manage_messages=True)
async def say(interaction: discord.Interaction, salon: discord.TextChannel, contenu: str):
    contenu_nettoye = contenu.replace("\\n", "\n")
    await salon.send(contenu_nettoye)
    await interaction.response.send_message(f"✅ Message envoyé dans {salon.mention}.", ephemeral=True)


# ============================
# === COMMANDES DE MODÉRATION ===
# ============================

@bot.tree.command(name="kick", description="Expulse un membre")
@discord.app_commands.describe(pseudo="Membre à expulser", raison="Raison du kick")
@discord.app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, pseudo: discord.Member, raison: str = "Aucune raison"):
    if est_bavure_raison(raison):
        embed = discord.Embed(
            title="⚠️ Bavure détectée",
            description=f"**Modérateur** : {interaction.user.mention}\n**Cible** : {pseudo.mention}\n**Commande** : /kick\n**Raison** : *{raison}*",
            color=0xff6600,
            timestamp=discord.utils.utcnow()
        )
        await send_log_to(bot, "bavures", embed)
        await interaction.response.send_message("❌ La raison est invalide (moins de 2 mots ou texte aléatoire).", ephemeral=True)
        return

    try:
        await pseudo.send(f"⚠️ Vous avez été expulsé de **{interaction.guild.name}** pour : **{raison}**.")
    except:
        pass
    await pseudo.kick(reason=raison)
    embed = discord.Embed(
        title="👢 Kick",
        description=f"**Membre** : {pseudo.mention}\n**Modérateur** : {interaction.user.mention}\n**Raison** : {raison}",
        color=0xff9900,
        timestamp=datetime.utcnow()
    )
    ch = get_sanction_channel(bot)
    if ch: 
        await ch.send(embed=embed)
    await interaction.response.send_message(f"✅ {pseudo.mention} expulsé.", ephemeral=True)

@bot.tree.command(name="ban", description="Bannit un membre")
@discord.app_commands.describe(pseudo="Membre à bannir", temps="Jours de suppression des messages (0 = aucun)", raison="Raison du ban")
@discord.app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, pseudo: discord.Member, temps: int = 0, raison: str = "Aucune raison"):
    if est_bavure_raison(raison):
        embed = discord.Embed(
            title="⚠️ Bavure détectée",
            description=f"**Modérateur** : {interaction.user.mention}\n**Cible** : {pseudo.mention}\n**Commande** : /ban\n**Raison** : *{raison}*",
            color=0xff6600,
            timestamp=discord.utils.utcnow()
        )
        await send_log_to(bot, "bavures", embed)
        await interaction.response.send_message("❌ La raison est invalide (moins de 2 mots ou texte aléatoire).", ephemeral=True)
        return

    try:
        await pseudo.send(f"⚠️ Vous avez été banni de **{interaction.guild.name}** pour : **{raison}**.")
    except:
        pass
    await pseudo.ban(reason=raison, delete_message_days=temps)
    embed = discord.Embed(
        title="🔨 Ban",
        description=f"**Membre** : {pseudo.mention}\n**Modérateur** : {interaction.user.mention}\n**Raison** : {raison}",
        color=0xff0000,
        timestamp=datetime.utcnow()
    )
    ch = get_sanction_channel(bot)
    if ch: 
        await ch.send(embed=embed)
    await interaction.response.send_message(f"✅ {pseudo.mention} banni.", ephemeral=True)

@bot.tree.command(name="warn", description="Avertit un membre")
@discord.app_commands.describe(pseudo="Membre à avertir", raison="Raison de l'avertissement")
@discord.app_commands.checks.has_permissions(manage_messages=True)
async def warn(interaction: discord.Interaction, pseudo: discord.Member, raison: str = "Aucune raison"):
    if est_bavure_raison(raison):
        embed = discord.Embed(
            title="⚠️ Bavure détectée",
            description=f"**Modérateur** : {interaction.user.mention}\n**Cible** : {pseudo.mention}\n**Commande** : /warn\n**Raison** : *{raison}*",
            color=0xff6600,
            timestamp=discord.utils.utcnow()
        )
        await send_log_to(bot, "bavures", embed)
        await interaction.response.send_message("❌ La raison est invalide (moins de 2 mots ou texte aléatoire).", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚠️ Avertissement",
        description=f"**Membre** : {pseudo.mention}\n**Modérateur** : {interaction.user.mention}\n**Raison** : {raison}",
        color=0xffff00,
        timestamp=discord.utils.utcnow()
    )
    ch = get_sanction_channel(bot)
    if ch: 
        await ch.send(embed=embed)
    await interaction.response.send_message(f"✅ Avertissement envoyé.", ephemeral=True)

@bot.tree.command(name="anti-spam", description="Active/désactive l'anti-spam")
@discord.app_commands.checks.has_permissions(administrator=True)
async def anti_spam(interaction: discord.Interaction, actif: bool):
    config.CONFIG["security"]["anti_spam"] = actif
    await interaction.response.send_message(f"✅ Anti-spam {'activé' if actif else 'désactivé'}.", ephemeral=True)

@bot.tree.command(name="anti-raid", description="Active/désactive l'anti-raid")
@discord.app_commands.checks.has_permissions(administrator=True)
async def anti_raid(interaction: discord.Interaction, actif: bool):
    config.CONFIG["security"]["anti_raid"] = actif
    await interaction.response.send_message(f"✅ Anti-raid {'activé' if actif else 'désactivé'}.", ephemeral=True)

@bot.tree.command(name="anti-hack", description="Active/désactive l'anti-hack")
@discord.app_commands.checks.has_permissions(administrator=True)
async def anti_hack(interaction: discord.Interaction, actif: bool):
    config.CONFIG["security"]["anti_hack"] = actif
    await interaction.response.send_message(f"✅ Anti-hack {'activé' if actif else 'désactivé'}.", ephemeral=True)


# Commande utilitaire pour forcer la synchronisation des commandes sur le serveur courant
@bot.tree.command(name="sync", description="(Admin) Synchronise les commandes pour ce serveur")
@discord.app_commands.checks.has_permissions(administrator=True)
async def sync_commands(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    try:
        if guild:
            # Copier les commandes globales vers le guild et synchroniser
            bot.tree.copy_global_to(guild=discord.Object(id=guild.id))
            synced = await bot.tree.sync(guild=discord.Object(id=guild.id))
            await interaction.followup.send(f"✅ {len(synced)} commandes synchronisées pour ce serveur.", ephemeral=True)
        else:
            synced = await bot.tree.sync()
            await interaction.followup.send(f"✅ {len(synced)} commandes globales synchronisées.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Échec de la sync: {e}", ephemeral=True)


# ============================
# === COMMANDES DE TICKETS ===
# ============================

@bot.tree.command(name="ticket-config", description="Configurer le système de tickets")
@discord.app_commands.checks.has_permissions(administrator=True)
async def ticket_config(interaction: discord.Interaction):
    """Configurer le mode de tickets (basic/advanced) et les options personnalisées"""
    used_defer = False
    try:
        await interaction.response.defer(ephemeral=True)
        used_defer = True
    except Exception:
        used_defer = False

    embed = discord.Embed(
        title="🎟️ Configuration Tickets",
        description="Choisissez le mode de fonctionnement",
        color=0x5865F2
    )
    try:
        if used_defer:
            await interaction.followup.send(embed=embed, view=TicketConfigView(source_channel=None), ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=TicketConfigView(source_channel=None), ephemeral=True)
    except Exception as e:
        # Fallback: try to send a simple ephemeral message
        try:
            await interaction.response.send_message("❌ Impossible d'ouvrir l'interface de configuration.", ephemeral=True)
        except:
            pass


class TicketConfigView(discord.ui.View):
    """Interface de configuration du mode tickets"""
    def __init__(self, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.source_channel = source_channel
    
    @discord.ui.button(label="Basic Mode", style=discord.ButtonStyle.primary, emoji="⚙️")
    async def basic_mode(self, interaction: discord.Interaction, button: discord.ui.Button):
        config.CONFIG["ticket_config"]["mode"] = "basic"
        config.CONFIG["ticket_config"]["options"] = [
            "Support Général",
            "Bug Report",
            "Suggestion",
            "Autre"
        ]
        embed = discord.Embed(
            title="✅ Mode Basic Activé",
            description="Options par défaut:\n• Support Général\n• Bug Report\n• Suggestion\n• Autre",
            color=0x2ecc71
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        # Si appelé depuis /start, envoyer POUR_TOI.txt
        await self._send_guide_if_needed()
        # Sauvegarder la configuration dans le salon de sauvegarde
        try:
            await save_guild_config(interaction.guild, config.CONFIG)
        except Exception:
            pass
    
    @discord.ui.button(label="Advanced Mode", style=discord.ButtonStyle.success, emoji="✨")
    async def advanced_mode(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="✨ Mode Advanced",
            description="Vous allez créer vos propres options.\n\nEnvoyez le texte pour la première option (ex: 'Bug Report')",
            color=0x5865F2
        )
        
        source_ch = self.source_channel
        
        class OptionsModal(discord.ui.Modal, title="Nouvelle option de ticket"):
            option_input = discord.ui.TextInput(label="Option (100 max chars)", placeholder="Bug Report", max_length=100)
            
            async def on_submit(self, modal_interaction: discord.Interaction):
                option_text = self.option_input.value.strip()
                config.CONFIG["ticket_config"]["mode"] = "advanced"
                config.CONFIG["ticket_config"]["options"] = [option_text]
                
                # Demander si ajouter d'autres options
                class AddMoreView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=300)
                    
                    @discord.ui.button(label="Ajouter une autre option", style=discord.ButtonStyle.success)
                    async def add_more(self, add_interaction: discord.Interaction, add_button: discord.ui.Button):
                        class AddOptionModal(discord.ui.Modal, title="Ajouter une option"):
                            new_option = discord.ui.TextInput(label="Nouvelle option", placeholder="Support", max_length=100)
                            
                            async def on_submit(self, add_modal_interaction: discord.Interaction):
                                new_opt = self.new_option.value.strip()
                                config.CONFIG["ticket_config"]["options"].append(new_opt)
                                
                                # Demander encore?
                                embed_again = discord.Embed(
                                    title="✅ Option Ajoutée",
                                    description=f"'{new_opt}' a été ajoutée.\n\nVoulez-vous ajouter une autre option?",
                                    color=0x2ecc71
                                )
                                await add_modal_interaction.response.send_message(
                                    embed=embed_again, 
                                    view=AddMoreView(), 
                                    ephemeral=True
                                )
                        
                        await add_interaction.response.send_modal(AddOptionModal())
                    
                    @discord.ui.button(label="Finir la configuration", style=discord.ButtonStyle.secondary)
                    async def finish(self, finish_interaction: discord.Interaction, finish_button: discord.ui.Button):
                        options_str = "\n".join(f"• {opt}" for opt in config.CONFIG["ticket_config"]["options"])
                        embed_done = discord.Embed(
                            title="✅ Configuration Terminée",
                            description=f"**Mode**: Advanced\n**Options**:\n{options_str}",
                            color=0x2ecc71
                        )
                        await finish_interaction.response.send_message(embed=embed_done, ephemeral=True)
                        # Si appelé depuis /start, envoyer POUR_TOI.txt
                        if source_ch:
                            try:
                                file_path = Path('POUR_TOI.txt')
                                if file_path.exists():
                                    await source_ch.send(file=discord.File(str(file_path), filename='POUR_TOI.txt'))
                            except Exception as e:
                                print(f"[TicketConfig] Erreur envoi POUR_TOI.txt: {e}")
                        # Sauvegarder la configuration dans le salon de sauvegarde
                        try:
                            await save_guild_config(finish_interaction.guild, config.CONFIG)
                        except Exception:
                            pass
                
                embed_first = discord.Embed(
                    title="✅ Option Créée",
                    description=f"'{option_text}' a été ajoutée comme première option.\n\nVoulez-vous ajouter d'autres options?",
                    color=0x2ecc71
                )
                await modal_interaction.response.send_message(
                    embed=embed_first,
                    view=AddMoreView(),
                    ephemeral=True
                )
        
        await interaction.response.send_modal(OptionsModal())
    
    async def _send_guide_if_needed(self):
        """Envoie POUR_TOI.txt si source_channel est défini"""
        if self.source_channel:
            try:
                file_path = Path('POUR_TOI.txt')
                if file_path.exists():
                    await self.source_channel.send(file=discord.File(str(file_path), filename='POUR_TOI.txt'))
            except Exception as e:
                print(f"[TicketConfig] Erreur envoi POUR_TOI.txt: {e}")


@bot.tree.command(name="ticket-panel", description="Envoie le panneau de création de ticket")
@discord.app_commands.checks.has_permissions(administrator=True)
async def ticket_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎟️ Support - Créer un ticket",
        description="Cliquez sur le bouton ci-dessous pour ouvrir un ticket avec l'équipe.\n\n> ⚠️ **Abuse = Sanction**",
        color=0x2f3136,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Seiko Security • Système sécurisé")
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("✅ Pannel de tickets envoyé.", ephemeral=True)


@bot.tree.command(name="add-user", description="Ajoute un utilisateur au ticket courant")
@discord.app_commands.describe(member="Membre à ajouter au ticket")
@discord.app_commands.checks.has_permissions(manage_channels=True)
async def add_user(interaction: discord.Interaction, member: discord.Member):
    guild = interaction.guild
    channel = interaction.channel
    if not channel.name.startswith("ticket-") and not channel.name.startswith("close-"):
        await interaction.response.send_message("❌ Cette commande ne peut être utilisée que dans un ticket.", ephemeral=True)
        return

    try:
        await channel.set_permissions(member, read_messages=True, send_messages=True, add_reactions=True, attach_files=False, embed_links=False)
        await interaction.response.send_message(f"✅ {member.mention} ajouté au ticket.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur: {e}", ephemeral=True)


@bot.tree.command(name="remove-user", description="Retire un utilisateur du ticket courant")
@discord.app_commands.describe(member="Membre à retirer du ticket")
@discord.app_commands.checks.has_permissions(manage_channels=True)
async def remove_user(interaction: discord.Interaction, member: discord.Member):
    guild = interaction.guild
    channel = interaction.channel
    if not channel.name.startswith("ticket-") and not channel.name.startswith("close-"):
        await interaction.response.send_message("❌ Cette commande ne peut être utilisée que dans un ticket.", ephemeral=True)
        return

    try:
        # Retirer l'accès
        await channel.set_permissions(member, read_messages=False, send_messages=False, add_reactions=False)
        await interaction.response.send_message(f"✅ {member.mention} retiré du ticket.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur: {e}", ephemeral=True)


# ============================
# === VIEWS POUR CONFIG ===
# ============================

class ConfigMainView(discord.ui.View):
    def __init__(self, guild: discord.Guild = None):
        super().__init__(timeout=600)
        self.guild = guild

    @discord.ui.button(label="📋 Rôles & Salons", style=discord.ButtonStyle.blurple)
    async def roles_salons(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📋 Rôles & Salons",
            description="Configurez les rôles et salons importants",
            color=0x2ecc71
        )
        guild = self.guild or interaction.guild
        await interaction.response.edit_message(embed=embed, view=RolesSalonsView(guild))

    @discord.ui.button(label="📊 Logs", style=discord.ButtonStyle.green)
    async def logs_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📊 Configuration des Logs",
            description="Définissez les salons de logs",
            color=0x3498db
        )
        await interaction.response.edit_message(embed=embed, view=LogsConfigView(interaction.client, interaction.guild))

    @discord.ui.button(label="🛡️ Sécurité", style=discord.ButtonStyle.danger)
    async def security(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🛡️ Sécurité",
            description="Activez/désactivez les protections",
            color=0xe74c3c
        )
        await interaction.response.edit_message(embed=embed, view=SecurityConfigView())

    @discord.ui.button(label="✅ Terminer", style=discord.ButtonStyle.success)
    async def finish_and_send_guide(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Terminer la configuration (ne pas afficher le guide ici)."""
        await interaction.response.send_message("✅ Configuration enregistrée. Le guide et la sauvegarde sont disponibles dans le salon privé de sauvegarde.", ephemeral=True)


class RolesSalonsView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=600)
        self.guild = guild

    @discord.ui.button(label="🎫 Rôle de Base", style=discord.ButtonStyle.primary)
    async def set_default_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎫 Rôle de Base à l'Arrivée",
            description="Choisissez le rôle de base dans la liste",
            color=0x9b59b6
        )
        await interaction.response.edit_message(embed=embed, view=RoleSelectView(self.guild, "default", next_view_factory=lambda g: RolesSalonsView(g)))

    @discord.ui.button(label="👑 Rôle Admin", style=discord.ButtonStyle.primary)
    async def set_admin_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="👑 Sélectionner le Rôle Admin",
            description="Choisissez le rôle dans la liste",
            color=0x2ecc71
        )
        await interaction.response.edit_message(embed=embed, view=RoleSelectView(self.guild, "admin", next_view_factory=lambda g: RolesSalonsView(g)))

    @discord.ui.button(label="🛡️ Rôle Modérateur", style=discord.ButtonStyle.primary)
    async def set_mod_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🛡️ Sélectionner le Rôle Modérateur",
            description="Choisissez le rôle dans la liste",
            color=0x3498db
        )
        await interaction.response.edit_message(embed=embed, view=RoleSelectView(self.guild, "moderator", next_view_factory=lambda g: RolesSalonsView(g)))

    @discord.ui.button(label="🎯 Rôle Fondateur", style=discord.ButtonStyle.primary)
    async def set_founder_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎯 Sélectionner le Rôle Fondateur",
            description="Choisissez le rôle dans la liste",
            color=0xe74c3c
        )
        await interaction.response.edit_message(embed=embed, view=RoleSelectView(self.guild, "founder", next_view_factory=lambda g: RolesSalonsView(g)))

    @discord.ui.button(label="👋 Bienvenue/Adieu", style=discord.ButtonStyle.success)
    async def set_welcome_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="👋 Salons Bienvenue/Adieu",
            description="Sélectionnez les salons",
            color=0x2ecc71
        )
        await interaction.response.edit_message(embed=embed, view=WelcomeLeaveView(self.guild))

    @discord.ui.button(label="⬅️ Retour", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="⚙️ Configuration Seiko",
            description="Choisissez une section",
            color=discord.Color.blurple()
        )
        await interaction.response.edit_message(embed=embed, view=ConfigMainView(interaction.guild))


class WelcomeLeaveView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=600)
        self.guild = guild

    @discord.ui.button(label="💬 Salon Bienvenue", style=discord.ButtonStyle.success)
    async def welcome(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="💬 Sélectionner Salon Bienvenue",
            description="Choisissez le salon dans la liste",
            color=0x2ecc71
        )
        await interaction.response.edit_message(embed=embed, view=ChannelSelectView(self.guild, "welcome", next_view_factory=lambda g: RolesSalonsView(g), back_view_factory=lambda g: WelcomeLeaveView(g)))

    @discord.ui.button(label="👋 Salon Adieu", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="👋 Sélectionner Salon Adieu",
            description="Choisissez le salon dans la liste",
            color=0xe74c3c
        )
        await interaction.response.edit_message(embed=embed, view=ChannelSelectView(self.guild, "leave", next_view_factory=lambda g: RolesSalonsView(g), back_view_factory=lambda g: WelcomeLeaveView(g)))

    @discord.ui.button(label="⬅️ Retour", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📋 Rôles & Salons",
        )
        await interaction.response.edit_message(embed=embed, view=RolesSalonsView(self.guild))


class LogsConfigView(discord.ui.View):
    def __init__(self, bot, guild: discord.Guild = None):
        super().__init__(timeout=600)
        self.bot = bot
        self.guild = guild

    @discord.ui.button(label="🔍 Détecter Logs", style=discord.ButtonStyle.primary)
    async def detect_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        missing_logs = []
        log_types = ["messages", "moderation", "ticket", "vocal", "securite"]
        
        for log_type in log_types:
            channel_id = config.CONFIG.get("logs", {}).get(log_type)
            if not channel_id or not guild.get_channel(channel_id):
                missing_logs.append(log_type)
        
        if missing_logs:
            msg = f"❌ **Salons de logs manquants** :\n" + "\n".join(f"  • {log}" for log in missing_logs)
            msg += f"\n\n✅ Utilisez `/add-cat-log` pour créer automatiquement tous les salons"
        else:
            msg = "✅ Tous les salons de logs sont configurés!"
        
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="➕ Ajouter Logs Auto", style=discord.ButtonStyle.success)
    async def auto_add_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Exécution de `/add-cat-log`...", ephemeral=True)
        # Cette commande existe déjà

    @discord.ui.button(label="⬅️ Retour", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="⚙️ Configuration Seiko",
            description="Choisissez une section",
            color=discord.Color.blurple()
        )
        guild = self.guild or interaction.guild
        await interaction.response.edit_message(embed=embed, view=ConfigMainView(guild))


class SecurityConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="🚫 Anti-Spam", style=discord.ButtonStyle.danger)
    async def toggle_spam(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = config.CONFIG["security"].get("anti_spam", False)
        config.CONFIG["security"]["anti_spam"] = not current
        status = "✅ Activé" if not current else "❌ Désactivé"
        await interaction.response.send_message(f"🚫 Anti-Spam : {status}", ephemeral=True)

    @discord.ui.button(label="🎯 Anti-Raid", style=discord.ButtonStyle.danger)
    async def toggle_raid(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = config.CONFIG["security"].get("anti_raid", False)
        config.CONFIG["security"]["anti_raid"] = not current
        status = "✅ Activé" if not current else "❌ Désactivé"
        await interaction.response.send_message(f"🎯 Anti-Raid : {status}", ephemeral=True)

    @discord.ui.button(label="🔐 Anti-Hack", style=discord.ButtonStyle.danger)
    async def toggle_hack(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = config.CONFIG["security"].get("anti_hack", False)
        config.CONFIG["security"]["anti_hack"] = not current
        status = "✅ Activé" if not current else "❌ Désactivé"
        await interaction.response.send_message(f"🔐 Anti-Hack : {status}", ephemeral=True)

    @discord.ui.button(label="📊 État", style=discord.ButtonStyle.blurple)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        spam = "✅" if config.CONFIG["security"].get("anti_spam") else "❌"
        raid = "✅" if config.CONFIG["security"].get("anti_raid") else "❌"
        hack = "✅" if config.CONFIG["security"].get("anti_hack") else "❌"
        
        embed = discord.Embed(
            title="🛡️ État de la Sécurité",
            description=f"{spam} Anti-Spam\n{raid} Anti-Raid\n{hack} Anti-Hack",
            color=0xe74c3c
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="⬅️ Retour", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="⚙️ Configuration Seiko",
            description="Choisissez une section",
            color=discord.Color.blurple()
        )
        await interaction.response.edit_message(embed=embed, view=ConfigMainView(interaction.guild))

    @discord.ui.button(label="🔤 Autorisations", style=discord.ButtonStyle.secondary)
    async def set_permissions(self, interaction: discord.Interaction, button: discord.ui.Button):
        class PermModal(discord.ui.Modal, title="Définir commandes autorisées pour un rôle"):
            role_input = discord.ui.TextInput(label="Rôle (mention ou ID)", placeholder="@Role ou 1234567890123456", required=True)
            cmds_input = discord.ui.TextInput(label="Commandes autorisées", placeholder="ping, ticket-panel, kick", required=True)
            async def on_submit(self, modal_interaction: discord.Interaction):
                role_val = self.role_input.value.strip()
                cmds_val = self.cmds_input.value.strip()
                import re
                m = re.search(r"(\d{17,20})", role_val)
                role_id = int(m.group(1)) if m else (int(role_val) if role_val.isdigit() else None)
                if not role_id:
                    await modal_interaction.response.send_message("❌ Rôle invalide.", ephemeral=True)
                    return
                cmds = [c.strip() for c in cmds_val.split(",") if c.strip()]
                config.CONFIG.setdefault("roles_permissions", {})[str(role_id)] = cmds
                await modal_interaction.response.send_message(f"✅ Permissions définies pour <@&{role_id}>: {', '.join(cmds)}", ephemeral=True)

        await interaction.response.send_modal(PermModal())


# ============================
# === COMMANDES DE CONFIGURATION ===
# ============================

@bot.tree.command(name="config", description="Configure le bot")
@discord.app_commands.checks.has_permissions(administrator=True)
async def config_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(
        title="⚙️ Configuration Seiko",
        description="Choisissez une section pour configurer le bot",
        color=discord.Color.blurple()
    )
    await interaction.followup.send(embed=embed, view=ConfigMainView(interaction.guild), ephemeral=True)


@bot.tree.command(name="salon-words", description="Active/désactive la détection de mots vulgaires dans les salons")
@discord.app_commands.describe(actif="True ou False")
@discord.app_commands.checks.has_permissions(administrator=True)
async def salon_words(interaction: discord.Interaction, actif: bool):
    config.CONFIG.setdefault("security", {})["filter_words"] = actif
    await interaction.response.send_message(f"✅ Détection des mots vulgaires {'activée' if actif else 'désactivée'}.", ephemeral=True)


@bot.tree.command(name="salon-links", description="Active/désactive la détection des liens dans les salons")
@discord.app_commands.describe(actif="True ou False")
@discord.app_commands.checks.has_permissions(administrator=True)
async def salon_links(interaction: discord.Interaction, actif: bool):
    config.CONFIG.setdefault("security", {})["filter_links"] = actif
    await interaction.response.send_message(f"✅ Détection des liens {'activée' if actif else 'désactivée'}.", ephemeral=True)


# ============================
# === COMMANDES DE SETUP ===
# ============================

class SetupStep0View(discord.ui.View):
    """Étape 0: Rôle de base à l'arrivée"""
    def __init__(self, guild: discord.Guild, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="🎫 Sélectionner Rôle de Base", style=discord.ButtonStyle.primary)
    async def select_base_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎓 Setup Seiko - Étape 0/6",
            description="Choisissez le rôle de base à donner à l'arrivée d'un nouveau membre",
            color=0x3498db
        )
        await interaction.response.edit_message(embed=embed, view=RoleSelectView(self.guild, "default", next_view_factory=lambda g: SetupStep1View(g, self.source_channel), back_view_factory=lambda g: SetupStep0View(g, self.source_channel)))

    @discord.ui.button(label="⏭️ Passer", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎓 Setup Seiko - Étape 1/6",
            description="**Rôles à l'arrivée d'un nouveau membre**\n\nQuels rôles doivent être attribués automatiquement à l'arrivée ?",
            color=0x3498db
        )
        await interaction.response.edit_message(embed=embed, view=SetupStep1View(self.guild, self.source_channel))


class SetupStep1View(discord.ui.View):
    """Étape 1: Rôle Admin"""
    def __init__(self, guild: discord.Guild, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="👑 Sélectionner Rôle Admin", style=discord.ButtonStyle.primary)
    async def select_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎓 Setup Seiko - Rôle Admin",
            description="Choisissez le rôle admin dans la liste",
            color=0x3498db
        )
        await interaction.response.edit_message(embed=embed, view=RoleSelectView(self.guild, "admin", next_view_factory=lambda g: SetupStep2View(g, self.source_channel), back_view_factory=lambda g: SetupStep1View(g, self.source_channel)))


class SetupStep2View(discord.ui.View):
    """Étape 2: Rôle Modérateur"""
    def __init__(self, guild: discord.Guild, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="🛡️ Sélectionner Rôle Modérateur", style=discord.ButtonStyle.primary)
    async def select_mod(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎓 Setup Seiko - Rôle Modérateur",
            description="Choisissez le rôle modérateur dans la liste",
            color=0x3498db
        )
        await interaction.response.edit_message(embed=embed, view=RoleSelectView(self.guild, "moderator", next_view_factory=lambda g: SetupStep3View(g, self.source_channel), back_view_factory=lambda g: SetupStep2View(g, self.source_channel)))


class SetupStep3View(discord.ui.View):
    """Étape 3: Rôle Fondateur"""
    def __init__(self, guild: discord.Guild, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="🎯 Sélectionner Rôle Fondateur", style=discord.ButtonStyle.primary)
    async def select_founder(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎓 Setup Seiko - Rôle Fondateur",
            description="Choisissez le rôle fondateur dans la liste",
            color=0x3498db
        )
        await interaction.response.edit_message(embed=embed, view=RoleSelectView(self.guild, "founder", next_view_factory=lambda g: SetupStep4View(g, self.source_channel), back_view_factory=lambda g: SetupStep3View(g, self.source_channel)))


class SetupStep4View(discord.ui.View):
    """Étape 4: Salon Bienvenue"""
    def __init__(self, guild: discord.Guild, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="💬 Sélectionner Salon Bienvenue", style=discord.ButtonStyle.success)
    async def select_welcome(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎓 Setup Seiko - Salon Bienvenue",
            description="Choisissez le salon bienvenue dans la liste",
            color=0x3498db
        )
        await interaction.response.edit_message(embed=embed, view=ChannelSelectView(self.guild, "welcome", next_view_factory=lambda g: SetupStep5View(g, self.source_channel), back_view_factory=lambda g: SetupStep4View(g, self.source_channel)))


class SetupStep5View(discord.ui.View):
    """Étape 5: Salon Adieu"""
    def __init__(self, guild: discord.Guild, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="👋 Sélectionner Salon Adieu", style=discord.ButtonStyle.danger)
    async def select_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎓 Setup Seiko - Salon Adieu",
            description="Choisissez le salon adieu dans la liste",
            color=0x3498db
        )
        await interaction.response.edit_message(embed=embed, view=ChannelSelectView(self.guild, "leave", next_view_factory=lambda g: SetupFinishView(g, self.source_channel), back_view_factory=lambda g: SetupStep5View(g, self.source_channel)))


class SetupFinishView(discord.ui.View):
    """Étape Finale: Configuration des tickets + Création des logs"""
    def __init__(self, guild: discord.Guild = None, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="✅ Configurer Tickets", style=discord.ButtonStyle.success)
    async def configure_tickets(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🎟️ Configuration Tickets",
            description="Choisissez le mode de fonctionnement",
            color=0x5865F2
        )
        await interaction.response.edit_message(embed=embed, view=TicketConfigView(source_channel=self.source_channel))

    @discord.ui.button(label="⏭️ Passer", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Créer le salon Sauvegarde
        backup_channel = await create_backup_channel(self.guild)
        
        # Sauvegarder la configuration
        if backup_channel:
            await save_guild_config(self.guild, config.CONFIG)
        
        await interaction.response.send_message(
            "✅ **Setup Terminé!**\n\n"
            "🔒 Votre configuration a été sauvegardée dans le salon **📁-sauvegarde**\n"
            "⚠️ Ne supprimez pas ce fichier - le bot en a besoin pour se reconfigurer!\n\n"
            "Vous pouvez configurer les tickets plus tard avec `/ticket-config`",
            ephemeral=True
        )
        # Envoyer POUR_TOI.txt
        try:
            file_path = Path('POUR_TOI.txt')
            if self.source_channel and file_path.exists():
                await self.source_channel.send(file=discord.File(str(file_path), filename='POUR_TOI.txt'))
            elif self.source_channel:
                await self.source_channel.send("📖 Guide introuvable sur le serveur.")
        except Exception as e:
            print(f"[Setup] Erreur envoi POUR_TOI.txt: {e}")


@bot.tree.command(name="start", description="Tutoriel de configuration du serveur")
@discord.app_commands.checks.has_permissions(administrator=True)
async def start_setup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(
        title="🎓 Setup Seiko - Étape 0/6",
        description="**Rôle de base à l'arrivée**\n\nQuel rôle donner automatiquement à l'arrivée d'un nouveau membre ?",
        color=0x3498db
    )
    await interaction.followup.send(embed=embed, view=SetupStep0View(interaction.guild, interaction.channel), ephemeral=True)


# ============================
# === COMMANDES D'AUDIT ===
# ============================

@bot.tree.command(name="reachlog", description="Affiche le dernier log d'audit")
@discord.app_commands.checks.has_permissions(administrator=True)
async def reachlog(interaction: discord.Interaction):
    try:
        async for entry in interaction.guild.audit_logs(limit=1):
            log_msg = f"**{entry.action.name}**\n"
            log_msg += f"**Cible** : {getattr(entry, 'target', 'Inconnue')}\n"
            log_msg += f"**Auteur** : {entry.user}\n"
            log_msg += f"**Date** : <t:{int(entry.created_at.timestamp())}:R>"
            await interaction.response.send_message(log_msg, ephemeral=True)
            return
        await interaction.response.send_message("📭 Aucun log trouvé.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur : {e}", ephemeral=True)

@bot.tree.command(name="reach-id", description="Résout un ID Discord")
@discord.app_commands.describe(id="ID à résoudre")
@discord.app_commands.checks.has_permissions(administrator=True)
async def reach_id(interaction: discord.Interaction, id: str):
    try:
        obj_id = int(id)
    except ValueError:
        await interaction.response.send_message("❌ ID invalide. Doit être un nombre.", ephemeral=True)
        return

    guild = interaction.guild
    results = []

    member = guild.get_member(obj_id)
    if member:
        results.append(f"👤 **Membre** : {member.mention} (`{member}`)")

    channel = guild.get_channel(obj_id)
    if channel:
        results.append(f"💬 **Salon** : {channel.mention} (`{channel.name}`)")

    role = guild.get_role(obj_id)
    if role:
        results.append(f"👑 **Rôle** : {role.mention} (`{role.name}`)")

    if results:
        await interaction.response.send_message(
            f"🔍 Résultats pour l'ID `{id}` :\n" + "\n".join(results),
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"❌ Aucun utilisateur, salon ou rôle trouvé avec l'ID `{id}`.",
            ephemeral=True
        )


# ============================
# === COMMANDES DE SAUVEGARDE ===
# ============================

@bot.tree.command(name="save", description="Sauvegarde la configuration actuelle")
@discord.app_commands.checks.has_permissions(administrator=True)
async def save_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    # Créer le salon de sauvegarde si nécessaire
    backup_channel = await create_backup_channel(guild)

    # Sauvegarder la configuration
    try:
        await save_guild_config(guild, config.CONFIG)
        await interaction.followup.send(f"✅ Configuration sauvegardée dans {backup_channel.mention}.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erreur lors de la sauvegarde: {e}", ephemeral=True)


@bot.tree.command(name="load-save", description="Charge une sauvegarde depuis un salon de sauvegarde")
@discord.app_commands.describe(salon="Salon contenant la sauvegarde (choix via autocomplete)")
@discord.app_commands.checks.has_permissions(administrator=True)
async def load_save(interaction: discord.Interaction, salon: discord.TextChannel):
    """Recherche dans le salon donné une sauvegarde (fichier .json ou JSON dans le message) et la charge."""
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    loaded_config = None
    source_msg = None

    try:
        async for msg in salon.history(limit=300):
            # 1) vérifier les pièces jointes
            for att in msg.attachments:
                name = (att.filename or "").lower()
                if name.endswith(".json"):
                    try:
                        raw = await att.read()
                        import json
                        loaded_config = json.loads(raw.decode("utf-8", errors="ignore"))
                        source_msg = msg
                        break
                    except Exception:
                        continue
            if loaded_config:
                break

            # 2) essayer de parser du JSON dans le contenu du message
            if msg.content and ("{" in msg.content and "}" in msg.content):
                try:
                    import json, re
                    # extraire premier bloc JSON basique
                    m = re.search(r"(\{.*\})", msg.content, re.S)
                    if m:
                        candidate = m.group(1)
                        loaded_config = json.loads(candidate)
                        source_msg = msg
                        break
                except Exception:
                    pass

        if not loaded_config:
            await interaction.followup.send(f"❌ Aucune sauvegarde JSON trouvée dans {salon.mention}.", ephemeral=True)
            return

        # Tenter d'appliquer la configuration si la fonction existe dans utils.config_manager
        applied = None
        try:
            apply_fn = getattr(config_manager, "apply_guild_config", None)
            if apply_fn:
                # si c'est coroutine ou fonction sync, gérer les deux cas
                if asyncio.iscoroutinefunction(apply_fn):
                    applied = await apply_fn(bot, guild, loaded_config)
                else:
                    applied = apply_fn(bot, guild, loaded_config)
            else:
                # Pas d'apply disponible : on met simplement à jour la config en mémoire
                config.CONFIG.update(loaded_config)
                applied = loaded_config
        except Exception as e:
            await interaction.followup.send(f"⚠️ La sauvegarde a été chargée mais l'application automatique a échoué : {e}", ephemeral=True)
            # continuer : on sauvegarde quand même la donnée brute
            config.CONFIG.update(loaded_config)
            applied = loaded_config

        # Sauvegarder la configuration via la fonction existante
        try:
            await save_guild_config(guild, config.CONFIG)
        except Exception:
            pass

        # Préparer résumé succinct
        keys = ", ".join(sorted(list(applied.keys()))) if isinstance(applied, dict) else "données non structurées"
        summary = (
            f"✅ Sauvegarde chargée depuis {salon.mention}.\n"
            f"🔎 Message source : <@{source_msg.author.id}> (ID: {source_msg.id})\n"
            f"🗂️ Clés détectées : {keys}\n"
            f"💾 Configuration mise à jour en mémoire et sauvegardée."
        )
        await interaction.followup.send(summary, ephemeral=True)

        # Envoyer un log informatif
        try:
            embed = discord.Embed(title="🔄 Sauvegarde chargée", description=f"Sauvegarde appliquée pour `{guild.name}` depuis {salon.mention}", color=0x2ecc71, timestamp=datetime.utcnow())
            await send_log_to(bot, "commands", embed)
        except Exception:
            pass

    except Exception as e:
        await interaction.followup.send(f"❌ Erreur durant la lecture du salon : {e}", ephemeral=True)


# ============================
# === COMMANDES D'ASSISTANCE ===
# ============================

@bot.tree.command(name="aide", description="Obtenir de l'aide sur les commandes")
async def aide(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🆘 Aide - Commandes Seiko",
        description="Voici quelques-unes des commandes disponibles :",
        color=0x5865F2
    )
    
    # Ajouter les commandes de manière dynamique
    for command in bot.tree.get_commands():
        if command.parent is None:  # Seulement les commandes de premier niveau
            embed.add_field(
                name=f"/{command.name}",
                value=command.description or "Pas de description",
                inline=False
            )
    
    embed.set_footer(text="Utilisez /help <commande> pour plus de détails sur une commande.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="help", description="Obtenir de l'aide sur une commande spécifique")
@discord.app_commands.describe(commande="La commande sur laquelle vous avez besoin d'aide")
async def help_cmd(interaction: discord.Interaction, commande: str):
    command = bot.tree.get_command(commande)
    if command:
        embed = discord.Embed(
            title=f"🆘 Aide - Commande /{command.name}",
            description=command.description or "Pas de description",
            color=0x5865F2
        )
        # Ajouter les détails de la commande ici si nécessaire
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("❌ Commande inconnue.", ephemeral=True)


# ============================
# === COMMANDES DE TEST ===
# ============================

@bot.tree.command(name="test-embed", description="Envoyer un embed de test")
async def test_embed(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Ceci est un test",
        description="Ceci est un embed de test pour vérifier la mise en forme.",
        color=0x3498db
    )
    embed.add_field(name="Champ 1", value="Ceci est le champ 1", inline=True)
    embed.add_field(name="Champ 2", value="Ceci est le champ 2", inline=True)
    embed.set_footer(text="Ceci est un pied de page")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="test-button", description="Envoyer un message avec un bouton de test")
async def test_button(interaction: discord.Interaction):
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Cliquez-moi!", style=discord.ButtonStyle.primary))
    
    await interaction.response.send_message("Voici un bouton de test:", view=view, ephemeral=True)

@bot.tree.command(name="test-select", description="Envoyer un message avec un menu déroulant de test")
async def test_select(interaction: discord.Interaction):
    select = discord.ui.Select(
        placeholder="Choisissez une option...",
        options=[
            discord.SelectOption(label="Option 1", value="1"),
            discord.SelectOption(label="Option 2", value="2"),
            discord.SelectOption(label="Option 3", value="3")
        ]
    )
    
    async def select_callback(interaction: discord.Interaction):
        await interaction.response.send_message(f"Vous avez sélectionné l'option {select.values[0]}", ephemeral=True)
    
    select.callback = select_callback
    
    view = discord.ui.View()
    view.add_item(select)
    
    await interaction.response.send_message("Voici un menu déroulant de test:", view=view, ephemeral=True)


# ============================
# === COMMANDES DE DEBUG ===
# ============================

@bot.tree.command(name="debug-sentry", description="Tester l'envoi d'une erreur à Sentry")
async def debug_sentry(interaction: discord.Interaction):
    try:
        division_par_zero = 1 / 0  # Ceci va causer une exception
    except Exception as e:
        await interaction.response.send_message("✅ Erreur capturée et envoyée à Sentry.", ephemeral=True)
        import sentry_sdk
        sentry_sdk.capture_exception(e)
    else:
        await interaction.response.send_message("❌ Aucune erreur n'a été levée.", ephemeral=True)


@bot.tree.command(name="debug-log", description="Envoyer un message de log personnalisé")
@discord.app_commands.describe(message="Le message à envoyer dans les logs")
async def debug_log(interaction: discord.Interaction, message: str):
    try:
        await send_log_to(bot, "commands", f"Log de debug: {message}")
        await interaction.response.send_message("✅ Message de log envoyé.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur lors de l'envoi du log: {e}", ephemeral=True)


# ============================
# === COMMANDES GÉNÉRALES (suite) ===
# ============================

@bot.tree.command(name="about", description="Informations sur le bot")
async def about(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 À propos de Seiko Security",
        description="Seiko Security est un bot Discord avancé pour la modération, la sécurité et la gestion des tickets.",
        color=0x5865F2
    )
    embed.add_field(name="Créateur", value="VotreNom#1234", inline=True)
    embed.add_field(name="Serveur de support", value="Lien vers votre serveur", inline=True)
    embed.add_field(name="Version", value="1.0.0", inline=True)
    embed.set_footer(text="Merci d'utiliser Seiko Security!")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="invite", description="Obtenir le lien d'invitation du bot")
async def invite(interaction: discord.Interaction):
    await interaction.response.send_message("🔗 [Cliquez ici pour inviter Seiko Security sur votre serveur](https://discord.com/oauth2/authorize?client_id=VOTRE_CLIENT_ID&scope=bot&permissions=8)", ephemeral=True)

bot.run(config.DISCORD_TOKEN)