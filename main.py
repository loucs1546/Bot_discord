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
import traceback

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

        # Bouton de recherche (fallback si la liste est tronquée ou pour rechercher par nom/ID)
        class SearchRoleButton(discord.ui.Button):
            def __init__(self):
                super().__init__(label="🔎 Rechercher", style=discord.ButtonStyle.secondary)

            async def callback(inner_self, interaction: discord.Interaction):
                role_type_local = self.role_type  # capture pour le modal

                class RoleModal(discord.ui.Modal, title="Rechercher un rôle"):
                    query = discord.ui.TextInput(label="Mention / ID / Nom du rôle", placeholder="@Role ou 123... ou Nom du rôle", max_length=100)

                    async def on_submit(self, modal_interaction: discord.Interaction):
                        val = self.query.value.strip()
                        g = modal_interaction.guild
                        role = None
                        # essayer ID
                        if val.isdigit():
                            role = g.get_role(int(val))
                        else:
                            m = re.search(r"<@&(\d{17,20})>", val)
                            if m:
                                role = g.get_role(int(m.group(1)))
                        # sinon rechercher par nom (insensible)
                        if not role:
                            for r in g.roles:
                                if r.name.lower() == val.lower():
                                    role = r
                                    break
                        if not role:
                            await modal_interaction.response.send_message("❌ Rôle introuvable.", ephemeral=True)
                            return
                        # appliquer en utilisant la valeur capturée
                        config.CONFIG.setdefault("roles", {})[role_type_local] = role.id
                        await modal_interaction.response.send_message(f"✅ Rôle {role_type_local} défini : <@&{role.id}>", ephemeral=True)

                await interaction.response.send_modal(RoleModal())

        self.add_item(SearchRoleButton())


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

        # Bouton de recherche (fallback si la liste est tronquée ou pour rechercher par nom/ID)
        class SearchChannelButton(discord.ui.Button):
            def __init__(self):
                super().__init__(label="🔎 Rechercher", style=discord.ButtonStyle.secondary)

            async def callback(inner_self, interaction: discord.Interaction):
                channel_type_local = self.channel_type  # capture pour le modal

                class ChannelModal(discord.ui.Modal, title="Rechercher un salon"):
                    query = discord.ui.TextInput(label="Mention / ID / Nom du salon", placeholder="#salon ou 123... ou nom", max_length=100)

                    async def on_submit(self, modal_interaction: discord.Interaction):
                        val = self.query.value.strip()
                        g = modal_interaction.guild
                        ch = None
                        # ID
                        if val.isdigit():
                            ch = g.get_channel(int(val))
                        else:
                            m = re.search(r"<#(\d{17,20})>", val)
                            if m:
                                ch = g.get_channel(int(m.group(1)))
                        if not ch:
                            for c in g.channels:
                                if c.name.lower() == val.lower():
                                    ch = c
                                    break
                        if not ch:
                            await modal_interaction.response.send_message("❌ Salon introuvable.", ephemeral=True)
                            return
                        config.CONFIG.setdefault("channels", {})[channel_type_local] = ch.id
                        await modal_interaction.response.send_message(f"✅ Salon {channel_type_local} défini : <#{ch.id}>", ephemeral=True)

                await interaction.response.send_modal(ChannelModal())

        self.add_item(SearchChannelButton())


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
        # stocker la sélection dans la view parente et ack
        try:
            parent = self.view
            if parent is None:
                # si appelé hors contexte, répondre éphémère
                await interaction.response.send_message(f"✅ Sélection : {self.values[0]}", ephemeral=True)
                return
            parent.selected_option = self.values[0]
            # petit feedback discret
            await interaction.response.send_message(f"✅ Sélection : **{self.values[0]}** (prête à être créée)", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message("❌ Erreur en traitant la sélection.", ephemeral=True)

# Nouvelle view : TicketPanelView → panel unique contenant select + create
class TicketPanelView(discord.ui.View):
	def __init__(self, guild: discord.Guild):
		super().__init__(timeout=None)
		self.guild = guild
		self.selected_option = None

		mode = config.CONFIG.get("ticket_config", {}).get("mode", "basic")
		options = config.CONFIG.get("ticket_config", {}).get("options", [])

		# Si advanced, ajouter le select (limité à 25 par Discord)
		if mode != "basic":
			select = TicketChoiceSelect(guild)
			# override callback via la classe TicketChoiceSelect définie plus haut
			self.add_item(select)

		# Bouton créer (présent dans tous les modes)
		@discord.ui.button(label="📩 Créer le Ticket", style=discord.ButtonStyle.success)
		async def create_button(interaction: discord.Interaction, button: discord.ui.Button):
			# déterminer option
			selected = self.selected_option
			if mode == "basic":
				selected = options[0] if options else "Support Général"
			if not selected:
				await interaction.response.send_message("❌ Sélectionnez un type de ticket d'abord.", ephemeral=True)
				return

			guild = interaction.guild
			user = interaction.user

			# Vérifier ticket existant
			for channel in guild.channels:
				if channel.name.startswith("ticket-") and str(user.id) in channel.name:
					await interaction.response.send_message("Vous avez déjà un ticket ouvert !", ephemeral=True)
					return

			# Incrémenter le counter
			config.CONFIG.setdefault("ticket_config", {}).setdefault("counter", 0)
			ticket_num = config.CONFIG["ticket_config"]["counter"] + 1
			config.CONFIG["ticket_config"]["counter"] = ticket_num

			ticket_name = f"ticket-{str(ticket_num).zfill(6)}"
			overwrites = {
				guild.default_role: discord.PermissionOverwrite(read_messages=False),
				user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=False, embed_links=False),
				guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
			}

			try:
				ticket_channel = await guild.create_text_channel(name=ticket_name, overwrites=overwrites, reason=f"Ticket créé par {user} ({selected})")
			except Exception as e:
				await interaction.response.send_message(f"❌ Erreur création ticket: {e}", ephemeral=True)
				return

			embed = discord.Embed(title=f"🎟️ {selected} - #{ticket_num:06d}", description=f"{user.mention}, décrivez votre demande.", color=0x5865F2, timestamp=datetime.utcnow())
			view = TicketManagementView(user.id, ticket_num)
			await ticket_channel.send(embed=embed, view=view)

			# log
			try:
				log_embed = discord.Embed(title="🎟️ Ticket créé", description=f"**Utilisateur** : {user.mention}\n**Type** : {selected}\n**Ticket** : {ticket_channel.mention}", color=0x00ff00, timestamp=datetime.utcnow())
				await send_log_to(bot, "ticket", log_embed)
			except Exception:
				pass

			await interaction.response.send_message(f"✅ Ticket créé: {ticket_channel.mention}\n💬 Type: **{selected}**", ephemeral=True)

		# attacher le bouton créé dynamiquement à la view
		# (decorator a déjà lié la fonction ci-dessus)
		# nothing else here

# --- Remplacement : enregistrement programmatique et conditionnel de la commande /ticket-panel ---
# (évite CommandAlreadyRegistered si la commande existe déjà)

async def _ticket_panel_impl(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎟️ Support - Créer un ticket",
        description="Sélectionnez le type puis cliquez sur 'Créer le Ticket'.\n> ⚠️ Abuse = Sanction",
        color=0x2f3136,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Seiko Security • Système sécurisé")
    await interaction.channel.send(embed=embed, view=TicketPanelView(interaction.guild))
    await interaction.response.send_message("✅ Panneau de tickets envoyé.", ephemeral=True)

# Créer l'objet Command et l'ajouter seulement s'il n'existe pas encore
try:
    existing = bot.tree.get_command("ticket-panel")
except Exception:
    existing = None

if not existing:
    cmd = discord.app_commands.Command(
        name="ticket-panel",
        description="Envoie le panneau de création de ticket",
        callback=_ticket_panel_impl
    )
    try:
        bot.tree.add_command(cmd)
        print("✅ Commande /ticket-panel enregistrée dynamiquement")
    except Exception as e:
        print(f"❌ Échec ajout commande /ticket-panel dynamiquement: {e}")
else:
    print("ℹ️ /ticket-panel déjà enregistrée — enregistrement dynamique ignoré")

# --- SUPPRIMÉ : definition décorée redondante de /ticket-panel (dupliquée) ---
# La commande /ticket-panel est désormais enregistrée de façon programmatique
# plus haut dans le fichier pour éviter CommandAlreadyRegistered

# === EVENT: on_ready ===
@bot.event
async def on_ready():
    global cogs_loaded
    print("ℹ️ on_ready called")
    try:
        print(f"✅ Tentative d'initialisation pour {bot.user}...")
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
                    traceback.print_exc()
    
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
                traceback.print_exc()
            
            # ===== REMPLACÉ : plus de scan automatique des sauvegardes au démarrage =====
            print("\nℹ️ Le scan automatique des sauvegardes au démarrage a été désactivé.")
            print("ℹ️ Utilisez la commande /load-save <salon_de_sauvegarde> pour charger une configuration depuis un salon de sauvegarde.")
            
            cogs_loaded = True
            
            # AJOUTER LES VIEWS PERSISTANTES
            try:
                bot.add_view(TicketView())
                bot.add_view(TicketControls(0))
                print("✅ Views ticket enregistrées")
            except Exception as e:
                print(f"❌ Erreur enregistrement views persistantes: {e}")
                traceback.print_exc()
            
            # Démarrer la boucle de self-ping (anti-AFK via PING sur PUBLIC_URL)
            try:
                if not hasattr(bot, "self_ping_task") or bot.self_ping_task.done():
                    bot.self_ping_task = asyncio.create_task(self_ping_loop())
                    print("✅ Self-ping task démarrée")
            except Exception as e:
                print(f"❌ Impossible de démarrer self-ping task: {e}")
                traceback.print_exc()
    except Exception as e:
        print(f"❌ Exception dans on_ready: {e}")
        traceback.print_exc()


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

    # chercher une catégorie existante ressemblant à la catégorie de logs
    found_cat = None
    for category in guild.categories:
        low = category.name.lower()
        if "surveillance" in low or "surveillances" in low or "surveil" in low or "SURVEILLANCES" in category.name:
            found_cat = category
            break

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

    # si on trouve une catégorie, tenter de mapper ses salons existants vers les clés
    if found_cat:
        channel_ids = {}
        names_to_keys = {key: display for (display, key) in salon_configs}
        for c in found_cat.channels:
            cname = c.name.lower()
            for display, key in salon_configs:
                # match si le nom du salon contient la clé (ex: "messages") ou le libellé simplifié
                if key in cname or ''.join(ch for ch in display.lower() if ch.isalnum()) in ''.join(ch for ch in cname if ch.isalnum()):
                    channel_ids[key] = c.id
        if channel_ids:
            if not isinstance(config.CONFIG, dict):
                config.CONFIG = {}
            config.CONFIG.setdefault("logs", {})
            config.CONFIG["logs"].update(channel_ids)
            found_list = ", ".join(f"{k}: <#{v}>" for k, v in channel_ids.items())
            await interaction.followup.send(f"✅ Catégorie existante utilisée : **{found_cat.name}**. Salons mappés : {found_list}", ephemeral=True)
            return
        # sinon continuer vers la création (aucun salon pertinent trouvé)

    # création classique si aucune catégorie trouvée ou aucun salon pertinent
    try:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        category = await guild.create_category(
            name="𓆩𖤍𓆪۰⟣ SURVEILLANCES ⟢۰𓆩𖤍𓆪",
            overwrites=overwrites
        )

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


# === AJOUT : VUES DE SETUP (Étapes pour la commande /start) ===
class SetupStep0View(discord.ui.View):
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
            description="Rôles à configurer : Rôle Admin",
            color=0x3498db
        )
        await interaction.response.edit_message(embed=embed, view=SetupStep1View(self.guild, self.source_channel))


class SetupStep1View(discord.ui.View):
    def __init__(self, guild: discord.Guild, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="👑 Sélectionner Rôle Admin", style=discord.ButtonStyle.primary)
    async def select_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🎓 Setup Seiko - Rôle Admin", description="Choisissez le rôle admin dans la liste", color=0x3498db)
        await interaction.response.edit_message(embed=embed, view=RoleSelectView(self.guild, "admin", next_view_factory=lambda g: SetupStep2View(g, self.source_channel), back_view_factory=lambda g: SetupStep1View(g, self.source_channel)))


class SetupStep2View(discord.ui.View):
    def __init__(self, guild: discord.Guild, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="🛡️ Sélectionner Rôle Modérateur", style=discord.ButtonStyle.primary)
    async def select_mod(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🎓 Setup Seiko - Rôle Modérateur", description="Choisissez le rôle modérateur dans la liste", color=0x3498db)
        await interaction.response.edit_message(embed=embed, view=RoleSelectView(self.guild, "moderator", next_view_factory=lambda g: SetupStep3View(g, self.source_channel), back_view_factory=lambda g: SetupStep2View(g, self.source_channel)))


class SetupStep3View(discord.ui.View):
    def __init__(self, guild: discord.Guild, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="🎯 Sélectionner Rôle Fondateur", style=discord.ButtonStyle.primary)
    async def select_founder(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🎓 Setup Seiko - Rôle Fondateur", description="Choisissez le rôle fondateur dans la liste", color=0x3498db)
        await interaction.response.edit_message(embed=embed, view=RoleSelectView(self.guild, "founder", next_view_factory=lambda g: SetupStep4View(g, self.source_channel), back_view_factory=lambda g: SetupStep3View(g, self.source_channel)))


class SetupStep4View(discord.ui.View):
    def __init__(self, guild: discord.Guild, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="💬 Sélectionner Salon Bienvenue", style=discord.ButtonStyle.success)
    async def select_welcome(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🎓 Setup Seiko - Salon Bienvenue", description="Choisissez le salon bienvenue dans la liste", color=0x3498db)
        await interaction.response.edit_message(embed=embed, view=ChannelSelectView(self.guild, "welcome", next_view_factory=lambda g: SetupStep5View(g, self.source_channel), back_view_factory=lambda g: SetupStep4View(g, self.source_channel)))


class SetupStep5View(discord.ui.View):
    def __init__(self, guild: discord.Guild, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="👋 Sélectionner Salon Adieu", style=discord.ButtonStyle.danger)
    async def select_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🎓 Setup Seiko - Salon Adieu", description="Choisissez le salon adieu dans la liste", color=0x3498db)
        await interaction.response.edit_message(embed=embed, view=ChannelSelectView(self.guild, "leave", next_view_factory=lambda g: SetupFinishView(g, self.source_channel), back_view_factory=lambda g: SetupStep5View(g, self.source_channel)))


class SetupFinishView(discord.ui.View):
    def __init__(self, guild: discord.Guild = None, source_channel: discord.TextChannel = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.source_channel = source_channel

    @discord.ui.button(label="✅ Configurer Tickets", style=discord.ButtonStyle.success)
    async def configure_tickets(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🎟️ Configuration Tickets", description="Choisissez le mode de fonctionnement", color=0x5865F2)
        await interaction.response.edit_message(embed=embed, view=TicketConfigView(source_channel=self.source_channel))

    @discord.ui.button(label="⏭️ Passer", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Créer le salon Sauvegarde et sauvegarder la config si possible
        try:
            backup_channel = await create_backup_channel(self.guild)
            if backup_channel:
                await save_guild_config(self.guild, config.CONFIG)
        except Exception:
            pass

        await interaction.response.send_message(
            "✅ **Setup Terminé!**\n\n"
            "🔒 Votre configuration a été sauvegardée dans le salon de sauvegarde (si présent).\n"
            "Vous pouvez configurer les tickets plus tard avec `/ticket-config`",
            ephemeral=True
        )
        # Envoyer guide si possible
        try:
            file_path = Path('POUR_TOI.txt')
            if self.source_channel and file_path.exists():
                await self.source_channel.send(file=discord.File(str(file_path), filename='POUR_TOI.txt'))
            elif self.source_channel:
                await self.source_channel.send("📖 Guide introuvable sur le serveur.")
        except Exception:
            pass

# === AJOUT : commande /start (tunnel du tutoriel de setup) ===
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

# ...existing code...