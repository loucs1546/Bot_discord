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
import json
from datetime import datetime, timezone

timestamp=datetime.now(timezone.utc)
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

# === UTILITAIRES TICKETS POUR /start ===
class ContinueOptionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.continue_adding = False
    @discord.ui.button(label="✅ Oui", style=discord.ButtonStyle.success)
    async def yes(self, i: discord.Interaction, _):
        self.continue_adding = True
        await i.response.defer()
        self.stop()
    @discord.ui.button(label="❌ Non", style=discord.ButtonStyle.danger)
    async def no(self, i: discord.Interaction, _):
        self.continue_adding = False
        await i.response.defer()
        self.stop()

class TicketOptionCollectModal(discord.ui.Modal, title="Nouvelle option de ticket"):
    def __init__(self, num):
        super().__init__()
        self.num = num
        self.add_item(discord.ui.TextInput(label=f"Option {num}", placeholder="Support technique", max_length=50))
    async def on_submit(self, interaction: discord.Interaction):
        self.value = self.children[0].value.strip()
        await interaction.response.defer()
        self.stop()

async def collect_ticket_options(interaction, guild):
    options = []
    while True:
        modal = TicketOptionCollectModal(len(options) + 1)
        await interaction.response.send_modal(modal)
        await modal.wait()
        if not modal.value:
            break
        options.append(modal.value)
        cont_view = ContinueOptionView()
        await interaction.followup.send("Ajouter une autre option ?", view=cont_view, ephemeral=False)
        await cont_view.wait()
        if not cont_view.continue_adding:
            break
    return options or ["Support Général"]


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


# === UTILITAIRES POUR /start (à placer AVANT la commande /start) ===
async def wait_for_channel_mention(interaction, guild, prompt="salon"):
    msg = await interaction.channel.send(f"📌 Mentionnez le **{prompt}** (ex: #général) :")
    try:
        def check(m): return m.author == interaction.user and m.channel == interaction.channel and len(m.channel_mentions) == 1
        response = await interaction.client.wait_for("message", check=check, timeout=120)
        await msg.delete()
        await response.delete()
        return response.channel_mentions[0]
    except asyncio.TimeoutError:
        await msg.delete()
        await interaction.channel.send("❌ Temps écoulé. Réessayez `/start`.")
        return None

async def wait_for_role_mention(interaction, guild, prompt="rôle"):
    msg = await interaction.channel.send(f"📌 Mentionnez le **{prompt}** (ex: @Modérateur) :")
    try:
        def check(m): return m.author == interaction.user and m.channel == interaction.channel and len(m.role_mentions) == 1
        response = await interaction.client.wait_for("message", check=check, timeout=120)
        await msg.delete()
        await response.delete()
        return response.role_mentions[0]
    except asyncio.TimeoutError:
        await msg.delete()
        await interaction.channel.send("❌ Temps écoulé. Réessayez `/start`.")
        return None


# === SELECT MENUS POUR CONFIG ===
class RoleSelect(discord.ui.Select):
    def __init__(self, role_type: str, roles, page=0, page_size=25):
        self.role_type = role_type
        self.roles = roles
        self.page = page
        self.page_size = page_size
        super().__init__(
            placeholder=f"Sélectionner le rôle {role_type}...",
            min_values=1,
            max_values=1
        )
        start = page * page_size
        end = start + page_size
        self.options = [
            discord.SelectOption(label=role.name, value=str(role.id))
            for role in roles[start:end]
            if role.name != "@everyone"
        ]
        if len(roles) > end:
            self.options.append(discord.SelectOption(label="➡️ Page suivante", value="__next__"))
        if page > 0:
            self.options.append(discord.SelectOption(label="⬅️ Page précédente", value="__prev__"))

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "__next__":
            await interaction.response.edit_message(
                view=RoleSelectView(self.view.guild, self.role_type, self.roles, self.page + 1, self.view.next_view_factory, self.view.back_view_factory)
            )
            return
        if self.values[0] == "__prev__":
            await interaction.response.edit_message(
                view=RoleSelectView(self.view.guild, self.role_type, self.roles, self.page - 1, self.view.next_view_factory, self.view.back_view_factory)
            )
            return
        role_id = int(self.values[0])
        config.CONFIG.setdefault("roles", {})[self.role_type] = role_id
        await interaction.response.send_message(f"✅ Rôle {self.role_type} défini : <@&{role_id}>", ephemeral=True)

class ChannelSelect(discord.ui.Select):
    def __init__(self, channel_type: str, channels, page=0, page_size=25):
        self.channel_type = channel_type
        self.channels = channels
        self.page = page
        self.page_size = page_size
        super().__init__(
            placeholder=f"Sélectionner le salon {channel_type}...",
            min_values=1,
            max_values=1
        )
        start = page * page_size
        end = start + page_size
        self.options = [
            discord.SelectOption(label=channel.name, value=str(channel.id))
            for channel in channels[start:end]
        ]
        if len(channels) > end:
            self.options.append(discord.SelectOption(label="➡️ Page suivante", value="__next__"))
        if page > 0:
            self.options.append(discord.SelectOption(label="⬅️ Page précédente", value="__prev__"))

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "__next__":
            await interaction.response.edit_message(
                view=ChannelSelectView(self.view.guild, self.channel_type, self.channels, self.page + 1, self.view.next_view_factory, self.view.back_view_factory)
            )
            return
        if self.values[0] == "__prev__":
            await interaction.response.edit_message(
                view=ChannelSelectView(self.view.guild, self.channel_type, self.channels, self.page - 1, self.view.next_view_factory, self.view.back_view_factory)
            )
            return
        channel_id = int(self.values[0])
        config.CONFIG.setdefault("channels", {})[self.channel_type] = channel_id
        await interaction.response.send_message(f"✅ Salon {self.channel_type} défini : <#{channel_id}>", ephemeral=True)

# === VIEWS AVEC SELECT MENUS ===
class RoleSelectView(discord.ui.View):
    def __init__(self, guild: discord.Guild, role_type: str, roles=None, page=0, next_view_factory: callable = None, back_view_factory: callable = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.role_type = role_type
        self.next_view_factory = next_view_factory
        self.back_view_factory = back_view_factory
        roles = roles or [r for r in guild.roles if r.name != "@everyone"]
        select = RoleSelect(role_type, roles, page)
        select.view = self
        self.add_item(select)
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
    def __init__(self, guild: discord.Guild, channel_type: str, channels=None, page=0, next_view_factory: callable = None, back_view_factory: callable = None):
        super().__init__(timeout=600)
        self.guild = guild
        self.channel_type = channel_type
        self.next_view_factory = next_view_factory
        self.back_view_factory = back_view_factory
        channels = channels or guild.text_channels
        select = ChannelSelect(channel_type, channels, page)
        select.view = self
        self.add_item(select)
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

@bot.tree.command(name="rule-config", description="Configurer le règlement du serveur")
@check_role_permissions("rule_config")
async def rule_config(interaction: discord.Interaction):
    class RuleModal(discord.ui.Modal, title="📝 Règlement du serveur"):
        content = discord.ui.TextInput(
            label="Contenu du règlement",
            style=discord.TextStyle.paragraph,
            placeholder="Ex: 1. Pas de spam...\n2. Respect mutuel...",
            max_length=4000,
            required=True
        )
        async def on_submit(self, i: discord.Interaction):
            config.CONFIG["rules"] = self.content.value
            await i.response.send_message("✅ Règlement enregistré.", ephemeral=True)

    await interaction.response.send_modal(RuleModal())

class RuleAcceptView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ J'accepte le règlement", style=discord.ButtonStyle.green, custom_id="rule_accept_final")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user

        wait_role = discord.utils.get(guild.roles, name="En attente de vérification")
        if wait_role and wait_role in user.roles:
            await user.remove_roles(wait_role)

        default_role_id = config.CONFIG.get("roles", {}).get("default")
        if default_role_id:
            default_role = guild.get_role(default_role_id)
            if default_role:
                await user.add_roles(default_role)

        await interaction.response.send_message("✅ Bienvenue sur le serveur !", ephemeral=True)

@bot.tree.command(name="reach-id", description="Obtenir le pseudo à partir d'une ID utilisateur")
@discord.app_commands.describe(user_id="ID de l'utilisateur")
@check_role_permissions("reach-id")
async def reach_id(interaction: discord.Interaction, user_id: str):
    try:
        uid = int(user_id)
    except ValueError:
        await interaction.response.send_message("❌ ID invalide. Doit être un nombre.", ephemeral=True)
        return

    user = interaction.guild.get_member(uid)
    if user:
        await interaction.response.send_message(f"✅ ID `{uid}` → **{user}** (`{user.name}#{user.discriminator}`)", ephemeral=True)
    else:
        # Essayer de récupérer depuis Discord (même hors serveur)
        try:
            user = await bot.fetch_user(uid)
            await interaction.response.send_message(f"✅ ID `{uid}` → **{user}** (hors serveur)", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message(f"❌ Aucun utilisateur trouvé avec l'ID `{uid}`.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur : {e}", ephemeral=True)

@bot.tree.command(name="rule", description="Afficher le règlement comme sur la photo")
async def rule(interaction: discord.Interaction):
    # === 1. Répondre immédiatement pour éviter le timeout ===
    await interaction.response.defer()  # 👈 Crucial

    guild = interaction.guild
    channel = interaction.channel

    # === 2. Vérifier qu’un règlement existe ===
    rules = config.CONFIG.get("rules")
    if not rules:
        await interaction.followup.send("❌ Aucun règlement configuré. Utilisez `/rule-config`.", ephemeral=True)
        return

    # === 3. S'assurer que le rôle "En attente" existe ===
    wait_role = discord.utils.get(guild.roles, name="En attente de vérification")
    if not wait_role:
        wait_role = await guild.create_role(
            name="En attente de vérification",
            color=discord.Color.dark_gray(),
            hoist=False,
            mentionable=False,
            reason="Système de règlement"
        )

    # === 4. Ne PAS boucler sur TOUS les salons (trop lent) ===
    # → Seulement bloquer les salons que le bot a déjà configurés (logs, etc.)
    # → OU laisser la gestion manuelle (recommandé pour éviter timeout)
    # → Ici, on ne touche qu'au salon courant
    await channel.set_permissions(wait_role, read_messages=True, send_messages=False)

    # === 5. Créer l'embed comme sur ta photo ===
    embed = discord.Embed(
        title="📜 Règlement Discord",
        color=0x2f3136,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(
        name="🔷 Règle Généraux",
        value="+ Avoir un Pseudo Rôles play\n+ Créer un environnement Sain",
        inline=False
    )
    embed.add_field(
        name="🔷 Règle Textuel",
        value=(
            "- Pas de Spam\n"
            "- Pas de Racisme, Politique\n"
            "- Pas de Harcèlement, Sexisme\n"
            "- Pas de Discours de Haine\n"
            "- Pas de Contenu NSFW\n"
            "- Pas De PUB MP\n"
            "- Pas de Spam Mention"
        ),
        inline=False
    )
    embed.add_field(
        name="🔷 Règle Vocal",
        value="- Aucun bruit gênant, fort ou aigu.\n- Aucune soundboard",
        inline=False
    )
    embed.set_image(url="https://i.imgur.com/7K9YhUa.png")
    embed.set_footer(text="L'équipe D'Impact Life")

    # === 6. Envoyer avec followup (pas response) ===
    view = RuleAcceptView()
    bot.add_view(view)  # pour persistance après reboot
    await interaction.followup.send(embed=embed, view=view)

class AdvancedTicketSelect(discord.ui.Select):
    def __init__(self, ticket_system: str):
        systems = config.CONFIG.get("ticket_systems", {})
        sys_conf = systems.get(ticket_system, {})
        options_list = sys_conf.get("options", ["Support Général"])
        select_options = []
        for i, opt in enumerate(options_list[:25]):
            value = f"opt_{i}"
            label = opt[:100]
            desc = opt[:50] if len(opt) > 50 else None
            select_options.append(discord.SelectOption(label=label, value=value, description=desc))
        super().__init__(
            placeholder="Sélectionnez le type de ticket...",
            options=select_options,
            min_values=1,
            max_values=1
        )
        self.ticket_system = ticket_system
        self.options_list = options_list

    async def callback(self, interaction: discord.Interaction):
        selected_index = int(self.values[0].replace("opt_", ""))
        selected_option = self.options_list[selected_index]
        guild = interaction.guild
        user = interaction.user

        # Empêcher doublons
        for ch in guild.channels:
            if ch.name.startswith("ticket-") and str(user.id) in ch.name:
                await interaction.response.send_message("❌ Vous avez déjà un ticket ouvert.", ephemeral=True)
                return

        systems = config.CONFIG.get("ticket_systems", {})
        sys_conf = systems.get(self.ticket_system)
        if not sys_conf:
            await interaction.response.send_message("❌ Système introuvable.", ephemeral=True)
            return

        counter = sys_conf.get("counter", 0) + 1
        config.CONFIG["ticket_systems"][self.ticket_system]["counter"] = counter

        # Nettoyer le pseudo
        clean_name = re.sub(r"[^a-zA-Z0-9\-_]", "", user.name.lower())
        if not clean_name:
            clean_name = f"user{user.id}"
        clean_name = clean_name[:20]
        ticket_name = f"{clean_name}-{str(counter).zfill(4)}"

        # === CRÉER / DÉTECTER LA CATÉGORIE TICKETS EXACTE ===
        ticket_category = discord.utils.get(guild.categories, name="𓆩𖤍𓆪۰⟣ TICKETS ⟢۰𓆩𖤍𓆪")
        if not ticket_category:
            overwrites_cat = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, manage_channels=True)
            }
            ticket_category = await guild.create_category(
                name="𓆩𖤍𓆪۰⟣ TICKETS ⟢۰𓆩𖤍𓆪",
                overwrites=overwrites_cat
            )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        }

        try:
            ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                category=ticket_category,
                overwrites=overwrites,
                reason=f"Ticket créé par {user} ({selected_option})"
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur : {e}", ephemeral=True)
            return

        # === Message dans le ticket ===
        embed = discord.Embed(
            title=f"🎟️ {selected_option} - #{counter:06d}",
            description=f"""Bonjour {user.mention},
    📝 Décrivez votre demande. Un membre de l’équipe vous répondra bientôt.
    > ⚠️ Pas de fichiers/liens.""",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Seiko Security")
        view = TicketManagementView(user.id, counter)
        await ticket_channel.send(embed=embed, view=view)

        # === Logs ===
        log_embed = discord.Embed(
            title="🎟️ Ticket créé",
            description=f"""**Utilisateur** : {user.mention}
    **Type** : {selected_option}
    **Ticket** : {ticket_channel.mention}""",
            color=0x00ff00,
            timestamp=datetime.now(timezone.utc)
        )
        log_embed.set_thumbnail(url=user.display_avatar.url)
        await send_log_to(bot, "ticket", log_embed)

        # === Réponse à l'utilisateur ===
        await interaction.response.send_message(
            f"✅ Ticket **{ticket_name}** créé : {ticket_channel.mention}",
            ephemeral=True
        )

class AdvancedTicketView(discord.ui.View):
    def __init__(self, ticket_system: str):
        super().__init__(timeout=None)
        self.add_item(AdvancedTicketSelect(ticket_system))

class BasicTicketView(discord.ui.View):
    def __init__(self, ticket_system: str):
        super().__init__(timeout=None)
        self.ticket_system = ticket_system

    @discord.ui.button(label="📩 Créer un ticket", style=discord.ButtonStyle.success, emoji="🎫")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user

        # Empêcher doublons
        for ch in guild.channels:
            if ch.name.startswith("ticket-") and str(user.id) in ch.name:
                await interaction.response.send_message("❌ Vous avez déjà un ticket ouvert.", ephemeral=True)
                return

        systems = config.CONFIG.get("ticket_systems", {})
        sys_conf = systems.get(self.ticket_system)
        if not sys_conf:
            await interaction.response.send_message("❌ Système introuvable.", ephemeral=True)
            return

        options = sys_conf.get("options", ["Support Général"])
        selected_option = options[0]  # Toujours le premier en mode basique
        counter = sys_conf.get("counter", 0) + 1
        config.CONFIG["ticket_systems"][self.ticket_system]["counter"] = counter

        # Nettoyer le pseudo (caractères invalides pour nom de salon)
        clean_name = re.sub(r"[^a-zA-Z0-9\-_]", "", user.name.lower())
        if not clean_name:
            clean_name = f"user{user.id}"
        clean_name = clean_name[:20]  # Limiter à 20 caractères
        ticket_name = f"{clean_name}-{str(counter).zfill(4)}"

        # === CRÉER / DÉTECTER LA CATÉGORIE TICKETS EXACTE ===
        ticket_category = discord.utils.get(guild.categories, name="𓆩𖤍𓆪۰⟣ TICKETS ⟢۰𓆩𖤍𓆪")
        if not ticket_category:
            overwrites_cat = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, manage_channels=True)
            }
            ticket_category = await guild.create_category(
                name="𓆩𖤍𓆪۰⟣ TICKETS ⟢۰𓆩𖤍𓆪",
                overwrites=overwrites_cat
            )

        # Permissions du salon
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        }

        try:
            ticket_channel = await guild.create_text_channel(
                name=ticket_name,
                category=ticket_category,
                overwrites=overwrites,
                reason=f"Ticket créé par {user} ({selected_option})"
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur : {e}", ephemeral=True)
            return

        # Message dans le ticket
        embed = discord.Embed(
            title=f"🎟️ {selected_option} - #{counter:06d}",
            description=f"""Bonjour {user.mention},
    📝 Décrivez votre demande. Un membre de l’équipe vous répondra bientôt.
    > ⚠️ Pas de fichiers/liens.""",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Seiko Security")
        view = TicketManagementView(user.id, counter)
        await ticket_channel.send(embed=embed, view=view)

        # Logs
        log_embed = discord.Embed(
            title="🎟️ Ticket créé",
            description=f"""**Utilisateur** : {user.mention}
    **Type** : {selected_option}
    **Ticket** : {ticket_channel.mention}""",
            color=0x00ff00,
            timestamp=datetime.now(timezone.utc)
        )
        log_embed.set_thumbnail(url=user.display_avatar.url)
        await send_log_to(bot, "ticket", log_embed)

        await interaction.response.send_message(
            f"✅ Ticket **{ticket_name}** créé : {ticket_channel.mention}",
            ephemeral=True
        )

class TicketManagementView(discord.ui.View):
    """Boutons de gestion du ticket (Claim, Close, Reopen, Delete)"""
    def __init__(self, owner_id: int, ticket_num: int = None):
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.ticket_num = ticket_num

    @discord.ui.button(label="👤 Claim", style=discord.ButtonStyle.primary, emoji="✋")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_ticket_permissions(interaction.user):
            await interaction.response.send_message("❌ Vous n’avez pas la permission.", ephemeral=True)
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
                # Récupérer tout l'historique du salon
                messages = []
                async for msg in self.ticket_channel.history(limit=1000, oldest_first=True):
                    if msg.author == confirm_interaction.client.user and msg.embeds:
                        continue  # ignorer le message initial du bot
                    content = f"[{msg.created_at.strftime('%H:%M')}] **{msg.author}**: {msg.content or '(aucun texte)'}"
                    if msg.attachments:
                        urls = ", ".join([a.url for a in msg.attachments])
                        content += f"\n📎 Fichiers : {urls}"
                    messages.append(content)
                full_log = "\n".join(messages) or "Aucun message dans le ticket."
                if len(full_log) > 4000:
                    full_log = full_log[:3997] + "..."

                # Envoyer dans #🎫・tickets
                log_channel = discord.utils.get(confirm_interaction.guild.text_channels, name="🎫・tickets")
                if log_channel:
                    embed = discord.Embed(
                        title=f"🗂️ Historique ticket - {self.ticket_channel.name}",
                        description=full_log,
                        color=0x5865F2,
                        timestamp=datetime.now(timezone.utc)  # ← CORRIGÉ
                    )
                    owner = confirm_interaction.guild.get_member(self.owner_id) if self.owner_id else None
                    if owner:
                        embed.set_author(name=f"Créé par {owner}", icon_url=owner.display_avatar.url)
                    await log_channel.send(embed=embed)

                # Supprimer le salon
                await self.ticket_channel.delete()

            @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, emoji="❌")
            async def cancel_delete(self, cancel_interaction: discord.Interaction, cancel_button: discord.ui.Button):
                await cancel_interaction.response.send_message("❌ Suppression annulée.", ephemeral=True)      

        await interaction.response.send_message(embed=embed, view=ConfirmDeleteView(interaction.channel, owner_id=self.owner_id, ticket_num=self.ticket_num), ephemeral=True)

def has_ticket_permissions(user: discord.Member) -> bool:
    allowed_ids = []
    for key in ("support", "moderator", "admin", "founder"):
        rid = config.CONFIG.get("roles", {}).get(key)
        if rid:
            allowed_ids.append(rid)
    return (
        user.guild_permissions.administrator or
        any(r.id in allowed_ids for r in user.roles)
    )

# TicketControls est maintenant un alias pour TicketManagementView (compatibilité)
class TicketControls(TicketManagementView):
    """Classe de compatibilité - anciennement gérait les tickets"""
    pass


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
                bot.add_view(RuleAcceptView())
                bot.add_view(TicketPanelMultiView({}))
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
# === COMMANDES DE CONFIGURATION INITIALE ===
#=============================


@bot.tree.command(name="start", description="Configurer complètement le bot pour ce serveur")
@check_role_permissions("start")
async def start_config(interaction: discord.Interaction):
    await interaction.response.send_message("🚀 **Démarrage de la configuration complète…**", ephemeral=False)
    guild = interaction.guild

    # --- Salons ---
    await interaction.channel.send("📌 **Étape 1/5** : Veuillez mentionner le **salon d'accueil** (arrivée des membres).")
    welcome_channel = await wait_for_channel_mention(interaction, guild)
    if not welcome_channel: return
    config.CONFIG.setdefault("channels", {})["welcome"] = welcome_channel.id

    await interaction.channel.send("📌 **Étape 2/5** : Veuillez mentionner le **salon de départ** (départ des membres).")
    leave_channel = await wait_for_channel_mention(interaction, guild)
    if not leave_channel: return
    config.CONFIG["channels"]["leave"] = leave_channel.id

    # --- Rôles ---
    await interaction.channel.send("📌 **Étape 3/5** : Mentionnez le **rôle par défaut** pour les nouveaux membres.")
    default_role = await wait_for_role_mention(interaction, guild)
    if not default_role: return
    config.CONFIG.setdefault("roles", {})["default"] = default_role.id

    await interaction.channel.send("📌 **Étape 4/5** : Mentionnez le **rôle support** (pour gérer les tickets).")
    support_role = await wait_for_role_mention(interaction, guild)
    if not support_role: return
    config.CONFIG["roles"]["support"] = support_role.id

    await interaction.channel.send("📌 **Étape 5/5** : Mentionnez les rôles **administrateur** et **fondateur**.")
    admin_role = await wait_for_role_mention(interaction, guild, "administrateur")
    if not admin_role: return
    config.CONFIG["roles"]["admin"] = admin_role.id

    founder_role = await wait_for_role_mention(interaction, guild, "fondateur")
    if not founder_role: return
    config.CONFIG["roles"]["founder"] = founder_role.id

    # --- DÉTECTION DES LOGS ---
    log_cat = None
    for cat in guild.categories:
        if "surveillances" in cat.name.lower() or "log" in cat.name.lower():
            log_cat = cat
            break

    if log_cat:
        await interaction.channel.send("✅ **Catégorie de logs détectée** : les salons existants seront utilisés.")
        # Mapper les salons existants
        mapping = {
            "messages": "messages",
            "vocal": "vocal",
            "ticket": "tickets",
            "moderation": "rôles",
            "securite": "alertes",
            "sanctions": "sanctions",
            "commands": "commandes",
            "profile": "profil",
            "content": "contenu",
            "alerts": "alertes",
            "giveaway": "giveaway",
            "bavures": "bavures"
        }
        found = {}
        for channel in log_cat.text_channels:
            for key, keyword in mapping.items():
                if keyword in channel.name.lower():
                    found[key] = channel.id
        config.CONFIG.setdefault("logs", {}).update(found)
    else:
        await interaction.channel.send("❓ **Aucune catégorie de logs trouvée.**\nSouhaitez-vous en créer une ?")
        log_view = LogCreationChoiceView()
        await interaction.channel.send(view=log_view)
        await log_view.wait()
        if log_view.create_logs:
            # Créer la catégorie + salons
            overwrites = {guild.default_role: discord.PermissionOverwrite(read_messages=False)}
            category = await guild.create_category(name="𓆩𖤍𓆪۰⟣ SURVEILLANCES ⟢۰𓆩𖤍𓆪", overwrites=overwrites)
            salon_configs = [
                ("📜・messages", "messages"),
                ("🎤・vocal", "vocal"),
                ("🎫・tickets", "ticket"),
                ("👑・rôles", "moderation"),
                ("🚨・alertes", "securite"),
                ("⚖️・sanctions", "sanctions"),
                ("🛠️・commandes", "commands"),
                ("📛・profil", "profile"),
                ("🔍・contenu", "content"),
                ("💥・bavures", "bavures")
            ]
            channel_ids = {}
            for name, key in salon_configs:
                ch = await guild.create_text_channel(name=name, category=category, overwrites=overwrites)
                channel_ids[key] = ch.id
            config.CONFIG.setdefault("logs", {}).update(channel_ids)
            await interaction.channel.send("✅ **Catégorie de logs créée avec succès !**")

    # --- SÉCURITÉ ---
    await interaction.channel.send("🛡️ **Configurer la sécurité**")
    sec_view = FinalSecurityConfigView()
    await interaction.channel.send("Activez/désactivez les protections :", view=sec_view)
    await sec_view.wait()

        # --- SAUVEGARDE & MESSAGE FINAL ---
    save_ch = discord.utils.get(guild.text_channels, name="📁-sauvegarde")
    if not save_ch:
        overwrites = {guild.default_role: discord.PermissionOverwrite(read_messages=False)}
        save_ch = await guild.create_text_channel("📁-sauvegarde", overwrites=overwrites)

    # Message "patienter"
    wait_msg = await interaction.channel.send("⏳ **Veuillez patienter...** Sauvegarde en cours.")

    # Créer le fichier POUR_TOI.txt
    import json, io
    data_str = json.dumps(config.CONFIG, indent=4, ensure_ascii=False)
    file = discord.File(io.BytesIO(data_str.encode()), filename="POUR_TOI.txt")
    await save_ch.send("💾 **Sauvegarde post-`/start`**", file=file)

    # Supprimer message en attente et envoyer confirmation
    await wait_msg.delete()
    await interaction.channel.send(
        "✅ **Configuration terminée !**\n"
        "🔧 Vous pouvez modifier les paramètres à tout moment avec `/config`.\n"
        "🎟️ Pour configurer des systèmes de tickets avancés, utilisez `/ticket-config`."
    )

@bot.tree.command(name="add-user", description="Ajoute un utilisateur à un salon (ticket ou autre)")
@discord.app_commands.describe(utilisateur="Utilisateur à ajouter", salon="Salon (optionnel, par défaut : ici)")
async def add_user(interaction: discord.Interaction, utilisateur: discord.Member, salon: discord.TextChannel = None):
    channel = salon or interaction.channel
    await channel.set_permissions(
        utilisateur,
        read_messages=True,
        send_messages=True,
        attach_files=False
    )
    await interaction.response.send_message(f"✅ {utilisateur.mention} ajouté à {channel.mention}.")

@bot.tree.command(name="remove-user", description="Retire un utilisateur d'un salon")
@discord.app_commands.describe(utilisateur="Utilisateur à retirer", salon="Salon (optionnel, par défaut : ici)")
async def remove_user(interaction: discord.Interaction, utilisateur: discord.Member, salon: discord.TextChannel = None):
    channel = salon or interaction.channel
    await channel.set_permissions(utilisateur, overwrite=None)
    await interaction.response.send_message(f"✅ {utilisateur.mention} retiré de {channel.mention}.")

@bot.tree.command(name="role-config", description="Configurer les rôles autorisés à utiliser certaines commandes")
@check_role_permissions("role_config")
async def role_config(interaction: discord.Interaction):
    class RoleConfigModal(discord.ui.Modal, title="Configurer les permissions"):
        mod_role = discord.ui.TextInput(label="Rôle Modérateur", placeholder="Mentionnez ou ID", max_length=50)
        support_role = discord.ui.TextInput(label="Rôle Support", placeholder="Mentionnez ou ID", max_length=50)
        async def on_submit(self, i: discord.Interaction):
            try:
                mod_id = int(self.mod_role.value.strip("<@&>"))
                support_id = int(self.support_role.value.strip("<@&>"))
                config.CONFIG.setdefault("allowed_roles", {})["moderator"] = mod_id
                config.CONFIG["allowed_roles"]["support"] = support_id
                await i.response.send_message("✅ Rôles configurés pour les commandes restreintes.", ephemeral=True)
                await save_guild_config(i.guild, config.CONFIG)
            except ValueError:
                await i.response.send_message("❌ ID ou mention invalide.", ephemeral=True)
    await interaction.response.send_modal(RoleConfigModal())

def has_restricted_role(interaction: discord.Interaction) -> bool:
    allowed = config.CONFIG.get("allowed_roles", {})
    user_roles = {r.id for r in interaction.user.roles}
    return (
        interaction.user.guild_permissions.administrator or
        allowed.get("moderator") in user_roles or
        allowed.get("support") in user_roles or
        interaction.user.id == interaction.guild.owner_id
    )

# === VIEWS POUR /start ===
class LogCreationChoiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.create_logs = None
    @discord.ui.button(label="✅ Oui, créer les logs", style=discord.ButtonStyle.success)
    async def yes(self, i, _):
        self.create_logs = True
        await i.response.send_message("✅ Création des logs activée.", ephemeral=True)
        self.stop()
    @discord.ui.button(label="❌ Non, ignorer", style=discord.ButtonStyle.danger)
    async def no(self, i, _):
        self.create_logs = False
        await i.response.send_message("❌ Aucun log ne sera créé.", ephemeral=True)
        self.stop()

class PersistentTicketPanelView(discord.ui.View):
    def __init__(self, system_name: str):
        super().__init__(timeout=None)
        self.system_name = system_name
        # Select avec custom_id fixe
        select = discord.ui.Select(
            custom_id=f"ticket_select_{system_name}",
            placeholder="Choisissez le type de ticket...",
            options=[
                discord.SelectOption(label=opt, value=opt)
                for opt in config.CONFIG.get("ticket_systems", {}).get(system_name, {}).get("options", ["Support Général"])[:25]
            ]
        )
        select.callback = self.select_callback
        self.add_item(select)

        button = discord.ui.Button(
            label="📩 Créer le Ticket",
            style=discord.ButtonStyle.success,
            emoji="🎫",
            custom_id=f"ticket_create_{system_name}"
        )
        button.callback = self.create_ticket
        self.add_item(button)

        self.selected_option = None

    async def select_callback(self, interaction: discord.Interaction):
        self.selected_option = interaction.data["values"][0]
        await interaction.response.defer()

    async def create_ticket(self, interaction: discord.Interaction):
        if not self.selected_option:
            await interaction.response.send_message("❌ Sélectionnez d’abord un type de ticket.", ephemeral=True)
            return
        # ... logique de création de ticket (copie de TicketChoiceView.create_button)
        # (tu peux extraire cette logique dans une fonction séparée)

class FinalSecurityConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.anti_spam = config.CONFIG.get("security", {}).get("anti_spam", True)
        self.anti_raid = config.CONFIG.get("security", {}).get("anti_raid", True)
        self.anti_hack = config.CONFIG.get("security", {}).get("anti_hack", True)
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        self.add_item(self._make_button("Anti-Spam", self.anti_spam, self.toggle_spam))
        self.add_item(self._make_button("Anti-Raid", self.anti_raid, self.toggle_raid))
        self.add_item(self._make_button("Anti-Hack", self.anti_hack, self.toggle_hack))
        finish_btn = discord.ui.Button(label="✅ Valider", style=discord.ButtonStyle.green, custom_id="finish_sec")
        finish_btn.callback = self.finish
        self.add_item(finish_btn)

    def _make_button(self, label, enabled, callback):
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary)
        btn.callback = callback
        return btn

    async def toggle_spam(self, i): self.anti_spam = not self.anti_spam; config.CONFIG.setdefault("security", {})["anti_spam"] = self.anti_spam; self.update_buttons(); await i.response.edit_message(view=self)
    async def toggle_raid(self, i): self.anti_raid = not self.anti_raid; config.CONFIG.setdefault("security", {})["anti_raid"] = self.anti_raid; self.update_buttons(); await i.response.edit_message(view=self)
    async def toggle_hack(self, i): self.anti_hack = not self.anti_hack; config.CONFIG.setdefault("security", {})["anti_hack"] = self.anti_hack; self.update_buttons(); await i.response.edit_message(view=self)

    async def finish(self, interaction: discord.Interaction):
        # Sauvegarde + confirmation
        await interaction.response.defer()  # Valide l'interaction pour éviter le "échec"
        self.stop()

@bot.tree.command(name="reset", description="Réinitialise TOUTES les données du bot pour ce serveur")
@check_role_permissions("reset")
async def reset_config(interaction: discord.Interaction):
    config.CONFIG.clear()
    # Optionnel : supprimer le salon 📁-sauvegarde
    save_ch = discord.utils.get(interaction.guild.text_channels, name="📁-sauvegarde")
    if save_ch:
        await save_ch.delete(reason="Réinitialisation via /reset")
    await interaction.response.send_message(
        "✅ Configuration **complètement réinitialisée**.\n"
        "Utilisez `/start` pour recommencer la configuration.",
        ephemeral=True
    )

@bot.tree.command(name="config", description="Modifier la configuration actuelle")
@check_role_permissions("config")
async def configs(interaction: discord.Interaction):
    class ConfigMainView(discord.ui.View):
        @discord.ui.button(label="👥 Rôles & Salons", style=discord.ButtonStyle.primary, emoji="👥")
        async def roles_btn(self, i, _):
            await i.response.send_message("...", view=ConfigRolesView(i), ephemeral=True)
        @discord.ui.button(label="📜 Logs", style=discord.ButtonStyle.secondary, emoji="📜")
        async def logs_btn(self, i, _):
            await i.response.send_message("...", view=ConfigLogsView(), ephemeral=True)
        @discord.ui.button(label="🛡️ Sécurité", style=discord.ButtonStyle.danger, emoji="🛡️")
        async def sec_btn(self, i, _):
            await i.response.send_message("...", view=SecurityConfigView(), ephemeral=True)
    await interaction.response.send_message("🔧 **Modifier la configuration**", view=ConfigMainView(), ephemeral=True)

class ConfigRolesView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=300)
        self.interaction = interaction

    async def update_config_and_save(self, interaction: discord.Interaction, key: str, value_id: int, is_role: bool = True):
        config.CONFIG.setdefault("roles" if is_role else "channels", {})[key] = value_id
        # Sauvegarde
        save_ch = discord.utils.get(interaction.guild.text_channels, name="📁-sauvegarde")
        if save_ch:
            import json, io
            data_str = json.dumps(config.CONFIG, indent=4, ensure_ascii=False)
            file = discord.File(io.BytesIO(data_str.encode()), filename="POUR_TOI.txt")
            await save_ch.send("💾 **Sauvegarde mise à jour**", file=file)
        await interaction.followup.send("✅ Modification enregistrée.", ephemeral=True)

    @discord.ui.button(label="Salon d'accueil", style=discord.ButtonStyle.secondary)
    async def welcome_btn(self, i: discord.Interaction, _):
        ch = await wait_for_channel_mention(i, i.guild, "salon d'accueil")
        if ch:
            await self.update_config_and_save(i, "welcome", ch.id, is_role=False)

    @discord.ui.button(label="Salon départ", style=discord.ButtonStyle.secondary)
    async def leave_btn(self, i: discord.Interaction, _):
        ch = await wait_for_channel_mention(i, i.guild, "salon de départ")
        if ch:
            await self.update_config_and_save(i, "leave", ch.id, is_role=False)

    @discord.ui.button(label="Rôle par défaut", style=discord.ButtonStyle.primary)
    async def default_role_btn(self, i: discord.Interaction, _):
        role = await wait_for_role_mention(i, i.guild, "rôle par défaut")
        if role:
            await self.update_config_and_save(i, "default", role.id, is_role=True)

    @discord.ui.button(label="Rôle support", style=discord.ButtonStyle.primary)
    async def support_role_btn(self, i: discord.Interaction, _):
        role = await wait_for_role_mention(i, i.guild, "rôle support")
        if role:
            await self.update_config_and_save(i, "support", role.id, is_role=True)

    @discord.ui.button(label="Rôle admin", style=discord.ButtonStyle.danger)
    async def admin_role_btn(self, i: discord.Interaction, _):
        role = await wait_for_role_mention(i, i.guild, "rôle administrateur")
        if role:
            await self.update_config_and_save(i, "admin", role.id, is_role=True)

    @discord.ui.button(label="Rôle fondateur", style=discord.ButtonStyle.danger)
    async def founder_role_btn(self, i: discord.Interaction, _):
        role = await wait_for_role_mention(i, i.guild, "rôle fondateur")
        if role:
            await self.update_config_and_save(i, "founder", role.id, is_role=True)

class ConfigLogsView(discord.ui.View):
    @discord.ui.button(label="🔄 Recréer logs", style=discord.ButtonStyle.danger)
    async def reset(self, i, _):
        await i.response.send_message("Utilisez `/add-cat-log` pour recréer les salons.", ephemeral=True)

# === UTILITAIRES POUR /start ===

async def prompt_channel(interaction: discord.Interaction, label: str):
    await interaction.followup.send(f"📌 Sélectionnez le **{label}** :", ephemeral=True)
    channel_msg = await interaction.channel.send("En attente de sélection...")
    try:
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel and len(m.channel_mentions) == 1
        msg = await bot.wait_for("message", check=check, timeout=120)
        await channel_msg.delete()
        await msg.delete()
        return msg.channel_mentions[0]
    except asyncio.TimeoutError:
        await channel_msg.delete()
        await interaction.followup.send(f"❌ Temps écoulé pour le {label}.", ephemeral=True)
        return None

async def prompt_role(interaction: discord.Interaction, label: str):
    await interaction.followup.send(f"📌 Mentionnez le **{label}** (ex: @Modérateur) :", ephemeral=True)
    role_msg = await interaction.channel.send("En attente...")
    try:
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel and len(m.role_mentions) == 1
        msg = await bot.wait_for("message", check=check, timeout=120)
        await role_msg.delete()
        await msg.delete()
        return msg.role_mentions[0]
    except asyncio.TimeoutError:
        await role_msg.delete()
        await interaction.followup.send(f"❌ Temps écoulé pour le {label}.", ephemeral=True)
        return None

class TicketModeChoiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.chosen_mode = None
    @discord.ui.button(label="Basique", style=discord.ButtonStyle.success)
    async def basic(self, i, _):
        self.chosen_mode = "basic"
        await i.response.defer()
        self.stop()
    @discord.ui.button(label="Avancé", style=discord.ButtonStyle.primary)
    async def advanced(self, i, _):
        self.chosen_mode = "advanced"
        await i.response.defer()
        self.stop()

class ContinueButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Continuer", style=discord.ButtonStyle.green)
    async def continue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ Continuation validée.", ephemeral=True)
        self.stop()

class SecurityConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.anti_spam = config.CONFIG.get("security", {}).get("anti_spam", False)
        self.anti_raid = config.CONFIG.get("security", {}).get("anti_raid", False)
        self.anti_hack = config.CONFIG.get("security", {}).get("anti_hack", False)
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        self.add_item(self._make_button("Anti-Spam", self.anti_spam, self.toggle_spam))
        self.add_item(self._make_button("Anti-Raid", self.anti_raid, self.toggle_raid))
        self.add_item(self._make_button("Anti-Hack", self.anti_hack, self.toggle_hack))
        finish_btn = discord.ui.Button(label="✅ Terminer", style=discord.ButtonStyle.green, custom_id="finish")
        finish_btn.callback = self.finish
        self.add_item(finish_btn)

    def _make_button(self, label, enabled, callback):
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary)
        btn.callback = callback
        return btn

    async def toggle_spam(self, i): self.anti_spam = not self.anti_spam; config.CONFIG.setdefault("security", {})["anti_spam"] = self.anti_spam; self.update_buttons(); await i.response.edit_message(view=self)
    async def toggle_raid(self, i): self.anti_raid = not self.anti_raid; config.CONFIG.setdefault("security", {})["anti_raid"] = self.anti_raid; self.update_buttons(); await i.response.edit_message(view=self)
    async def toggle_hack(self, i): self.anti_hack = not self.anti_hack; config.CONFIG.setdefault("security", {})["anti_hack"] = self.anti_hack; self.update_buttons(); await i.response.edit_message(view=self)

    async def finish(self, interaction: discord.Interaction):
        await interaction.response.send_message("✅ Configuration de sécurité sauvegardée.", ephemeral=True)
        self.stop()

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
@check_role_permissions("logs")
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
@check_role_permissions("scan-deleted")
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
@check_role_permissions("add-cat-log")
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

@bot.tree.command(name="create-salon", description="Crée un salon dans une catégorie")
@discord.app_commands.describe(
    nom="Nom du salon",
    categorie="Catégorie où créer le salon"
)
@check_role_permissions("create-salon")
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

def check_role_permissions(command_name: str):
    async def predicate(interaction: discord.Interaction) -> bool:
        # Admins toujours autorisés
        if interaction.user.guild_permissions.administrator:
            return True

        # Rôles définis dans la config
        allowed_roles = config.CONFIG.get("role_permissions", {})
        user_role_ids = {role.id for role in interaction.user.roles}

        # Vérifie chaque rôle : si l’un d’eux autorise la commande → OK
        for role_key in allowed_roles:
            role_id = config.CONFIG.get("roles", {}).get(role_key)
            if role_id and role_id in user_role_ids:
                if allowed_roles[role_key].get(command_name, False):
                    return True

        # Refus : envoyer un message clair
        await interaction.response.send_message(
            "❌ Vous n’avez pas la permission d’utiliser cette commande.", 
            ephemeral=True
        )
        return False
    return discord.app_commands.check(predicate)


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
@discord.app_commands.checks.has_permissions(manage_channels=True)
async def delete_salon(interaction: discord.Interaction, salon: discord.TextChannel):
    name = salon.name
    await salon.delete(reason=f"Supprimé par {interaction.user}")
    await interaction.response.send_message(f"✅ Salon **{name}** supprimé.", ephemeral=True)

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


# ============================
# === COMMANDES DE TICKETS MULTI-PANEL ===
# ============================

@bot.tree.command(name="ticket-config", description="Configurer le système de tickets")
@check_role_permissions("ticket-config")
async def ticket_config(interaction: discord.Interaction):
    """Configurer plusieurs systèmes de tickets"""
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(
        title="🎟️ Configuration Tickets",
        description="Voulez-vous modifier un panel existant ou en créer un nouveau ?",
        color=0x5865F2
    )
    await interaction.followup.send(embed=embed, view=TicketMultiConfigView(), ephemeral=True)

class TicketMultiConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        systems = config.CONFIG.setdefault("ticket_systems", {})
        for sys_name in systems:
            self.add_item(TicketSystemButton(sys_name))
        self.add_item(NewTicketSystemButton())

class TicketSystemButton(discord.ui.Button):
    def __init__(self, sys_name):
        super().__init__(label=f"Modifier : {sys_name}", style=discord.ButtonStyle.primary)
        self.sys_name = sys_name
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Configuration du système de ticket **{self.sys_name}**",
            view=TicketConfigView(ticket_system=self.sys_name), ephemeral=True
        )

class NewTicketSystemButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="➕ Nouveau système de tickets", style=discord.ButtonStyle.success)
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(NewTicketSystemModal())

class NewTicketSystemModal(discord.ui.Modal, title="Nouveau système de tickets"):
    name = discord.ui.TextInput(label="Nom du système", placeholder="Support VIP", max_length=32)
    async def on_submit(self, interaction: discord.Interaction):
        sys_name = self.name.value.strip()
        if not sys_name:
            await interaction.response.send_message("❌ Nom invalide.", ephemeral=True)
            return
        config.CONFIG.setdefault("ticket_systems", {})[sys_name] = {
            "mode": "basic",
            "options": ["Support Général", "Bug Report", "Suggestion", "Autre"],
            "counter": 0
        }
        await interaction.response.send_message(
            f"Système **{sys_name}** créé. Configurez-le ci-dessous.",
            view=TicketConfigView(ticket_system=sys_name), ephemeral=True
        )

class TicketConfigView(discord.ui.View):
    def __init__(self, source_channel: discord.TextChannel = None, ticket_system: str = None):
        super().__init__(timeout=600)
        self.source_channel = source_channel
        self.ticket_system = ticket_system
    @discord.ui.button(label="Basic Mode", style=discord.ButtonStyle.primary, emoji="⚙️")
    async def basic_mode(self, interaction: discord.Interaction, button: discord.ui.Button):
        sys = self.ticket_system or "default"
        config.CONFIG.setdefault("ticket_systems", {}).setdefault(sys, {})
        config.CONFIG["ticket_systems"][sys]["mode"] = "basic"
        config.CONFIG["ticket_systems"][sys]["options"] = [
            "Support Général",
            "Bug Report",
            "Suggestion",
            "Autre"
        ]
        await interaction.response.send_message(
            f"✅ Mode Basic activé pour **{sys}**.", ephemeral=True
        )
        try:
            await save_guild_config(interaction.guild, config.CONFIG)
        except Exception:
            pass
    @discord.ui.button(label="Advanced Mode", style=discord.ButtonStyle.success, emoji="✨")
    async def advanced_mode(self, interaction: discord.Interaction, button: discord.ui.Button):
        sys = self.ticket_system or "default"
        await interaction.response.send_modal(TicketOptionsModal(sys))

class TicketOptionsModal(discord.ui.Modal, title="Configurer les options du ticket"):
    def __init__(self, sys_name: str):
        super().__init__()
        self.sys_name = sys_name
        self.text_inputs = []
        # Discord limite à 5 champs dans une modal
        for i in range(1, 6):  # ← 1 à 5
            ti = discord.ui.TextInput(
                label=f"Option {i}",
                placeholder=f"Ex: Support technique",
                required=(i == 1),  # seule la première est obligatoire
                max_length=100
            )
            self.add_item(ti)
            self.text_inputs.append(ti)

    async def on_submit(self, interaction: discord.Interaction):
        opts = []
        for ti in self.text_inputs:
            val = ti.value.strip()
            if val:
                opts.append(val)
        if not opts:
            await interaction.response.send_message("❌ Au moins une option est requise.", ephemeral=True)
            return

        config.CONFIG.setdefault("ticket_systems", {}).setdefault(self.sys_name, {})
        config.CONFIG["ticket_systems"][self.sys_name]["mode"] = "advanced"
        config.CONFIG["ticket_systems"][self.sys_name]["options"] = opts
        message = f"✅ **{len(opts)} options** définies pour **{self.sys_name}** :\n" + "\n".join(f"• {o}" for o in opts)
        await interaction.response.send_message(message, ephemeral=True)
        try:
            await save_guild_config(interaction.guild, config.CONFIG)
        except Exception:
            pass

@bot.tree.command(name="ticket-panel", description="Envoie un panneau public de création de ticket")
@check_role_permissions("icket-panel")
async def ticket_panel(interaction: discord.Interaction):
    systems = config.CONFIG.get("ticket_systems", {})
    if not systems:
        await interaction.response.send_message("❌ Aucun système de ticket configuré. Utilisez `/ticket-config`.", ephemeral=True)
        return
    if len(systems) == 1:
        sys_name = next(iter(systems))
        await send_public_ticket_panel(interaction, sys_name)
        await interaction.response.send_message(f"✅ Panel **{sys_name}** envoyé.", ephemeral=True)
    else:
        view = TicketSystemChoiceView(systems, interaction.channel)
        await interaction.response.send_message("Choisissez le système de ticket à afficher publiquement :", view=view, ephemeral=True)


class TicketSystemChoiceView(discord.ui.View):
    def __init__(self, systems: dict, target_channel: discord.TextChannel):
        super().__init__(timeout=180)
        self.target_channel = target_channel
        for name in systems:
            self.add_item(TicketSystemSelectButton(name))

class TicketSystemSelectButton(discord.ui.Button):
    def __init__(self, sys_name: str):
        super().__init__(label=sys_name, style=discord.ButtonStyle.success)
        self.sys_name = sys_name

    async def callback(self, interaction: discord.Interaction):
        # ✅ Envoie d'abord une réponse
        await interaction.response.send_message(
            f"✅ Panel **{self.sys_name}** envoyé dans {interaction.channel.mention}.", 
            ephemeral=True
        )
        # ✅ Envoie ensuite le panel public
        await send_public_ticket_panel(interaction, self.sys_name)
        self.view.stop()

@bot.event
async def on_member_join(member: discord.Member):
    """Donne automatiquement le rôle 'En attente de vérification' à tout nouveau membre."""
    guild = member.guild
    wait_role = discord.utils.get(guild.roles, name="En attente de vérification")
    if not wait_role:
        # Créer le rôle si absent
        wait_role = await guild.create_role(
            name="En attente de vérification",
            color=discord.Color.dark_gray(),
            hoist=False,
            mentionable=False,
            reason="Système de règlement Seiko"
        )
    try:
        await member.add_roles(wait_role, reason="Nouveau membre - doit accepter le règlement")
    except Exception:
        pass  # ignore si permissions manquantes

async def send_public_ticket_panel(interaction: discord.Interaction, sys_name: str):
    systems = config.CONFIG.get("ticket_systems", {})
    sys_conf = systems.get(sys_name)
    if not sys_conf:
        return

    mode = sys_conf.get("mode", "basic")
    embed = discord.Embed(
        title="🎟️ Support",
        description="Cliquez sur le bouton ci-dessous pour ouvrir un ticket." if mode == "basic"
                    else "Sélectionnez le type de ticket ci-dessous.",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="Seiko Security")

    if mode == "basic":
        view = BasicTicketView(sys_name)
    else:
        view = AdvancedTicketView(sys_name)

    await interaction.channel.send(embed=embed, view=view)

class TicketPanelMultiView(discord.ui.View):
    def __init__(self, systems: dict):
        super().__init__(timeout=None)
        for sys_name in systems:
            self.add_item(TicketPanelButton(sys_name))

class TicketPanelButton(discord.ui.Button):
    def __init__(self, sys_name):
        super().__init__(label=sys_name, style=discord.ButtonStyle.success)
        self.sys_name = sys_name
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Panel pour **{self.sys_name}**",
            view=TicketView(ticket_system=self.sys_name),
            ephemeral=True
        )

class RolePermConfigView(discord.ui.View):
    def __init__(self, role_key: str, page: int = 0):
        super().__init__(timeout=600)
        self.role_key = role_key
        self.page = page
        self.permissions = config.CONFIG.setdefault("role_permissions", {}).setdefault(role_key, {})
        self.all_commands = [cmd.name for cmd in bot.tree.get_commands() if not cmd.parent]
        self.commands_per_page = 20  # ← Maximum sûr (5 lignes x 4 boutons)
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        start = self.page * self.commands_per_page
        end = start + self.commands_per_page
        page_commands = self.all_commands[start:end]

        for cmd in page_commands:
            enabled = self.permissions.get(cmd, False)
            btn = discord.ui.Button(
                label=cmd[:80],  # Limite Discord = 80 caractères
                style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
                row=min(len(self.children) // 5, 4)  # Forcer max 5 par ligne
            )
            btn.callback = self.make_callback(cmd)
            self.add_item(btn)

        # Boutons de navigation
        if self.page > 0:
            prev_btn = discord.ui.Button(label="⬅️ Précédent", style=discord.ButtonStyle.secondary, row=4)
            prev_btn.callback = self.go_prev
            self.add_item(prev_btn)

        if end < len(self.all_commands):
            next_btn = discord.ui.Button(label="Suivant ➡️", style=discord.ButtonStyle.secondary, row=4)
            next_btn.callback = self.go_next
            self.add_item(next_btn)

        # Bouton Valider
        finish_btn = discord.ui.Button(label="✅ Valider", style=discord.ButtonStyle.green, row=4)
        finish_btn.callback = self.finish
        self.add_item(finish_btn)

    def make_callback(self, cmd_name):
        async def callback(interaction: discord.Interaction):
            self.permissions[cmd_name] = not self.permissions.get(cmd_name, False)
            self.update_buttons()
            await interaction.response.edit_message(view=self)
        return callback

    async def go_prev(self, interaction: discord.Interaction):
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(view=self)

    async def go_next(self, interaction: discord.Interaction):
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(view=self)

    async def finish(self, interaction: discord.Interaction):
        # Sauvegarde
        save_ch = discord.utils.get(interaction.guild.text_channels, name="📁-sauvegarde")
        if save_ch:
            import json, io
            data_str = json.dumps(config.CONFIG, indent=4, ensure_ascii=False)
            file = discord.File(io.BytesIO(data_str.encode()), filename="POUR_TOI.txt")
            await save_ch.send("💾 **Permissions par rôle mises à jour**", file=file)
        await interaction.response.send_message("✅ Permissions sauvegardées.", ephemeral=True)
        self.stop()

# ============================
# === COMMANDES DE MODÉRATION 
# ============================

@bot.tree.command(name="role-perms", description="Configurer les permissions par rôle")
@check_role_permissions("role-perms")
async def role_perms(interaction: discord.Interaction):
    class RolePermMainView(discord.ui.View):
        @discord.ui.button(label="Rôle par défaut", style=discord.ButtonStyle.secondary)
        async def default_btn(self, i, _):
            await i.response.send_message("...", view=RolePermConfigView("default"), ephemeral=True)
        @discord.ui.button(label="Rôle support", style=discord.ButtonStyle.primary)
        async def support_btn(self, i, _):
            await i.response.send_message("...", view=RolePermConfigView("support"), ephemeral=True)
        @discord.ui.button(label="Rôle modérateur", style=discord.ButtonStyle.primary)
        async def moderator_btn(self, i, _):
            await i.response.send_message("...", view=RolePermConfigView("moderator"), ephemeral=True)
        @discord.ui.button(label="Rôle admin", style=discord.ButtonStyle.danger)
        async def admin_btn(self, i, _):
            await i.response.send_message("...", view=RolePermConfigView("admin"), ephemeral=True)
        @discord.ui.button(label="Rôle fondateur", style=discord.ButtonStyle.danger)
        async def founder_btn(self, i, _):
            await i.response.send_message("...", view=RolePermConfigView("founder"), ephemeral=True)
    await interaction.response.send_message("🔧 **Sélectionnez un rôle à configurer :**", view=RolePermMainView(), ephemeral=True)


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
        timestamp=datetime.now(timezone.utc)
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
        timestamp=datetime.now(timezone.utc)
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
@check_role_permissions("anti-spam")
async def anti_spam(interaction: discord.Interaction, actif: bool):
    config.CONFIG["security"]["anti_spam"] = actif
    await interaction.response.send_message(f"✅ Anti-spam {'activé' if actif else 'désactivé'}.", ephemeral=True)

@bot.tree.command(name="anti-raid", description="Active/désactive l'anti-raid")
@check_role_permissions("anti-raid")
async def anti_raid(interaction: discord.Interaction, actif: bool):
    config.CONFIG["security"]["anti_raid"] = actif
    await interaction.response.send_message(f"✅ Anti-raid {'activé' if actif else 'désactivé'}.", ephemeral=True)

@bot.tree.command(name="anti-hack", description="Active/désactive l'anti-hack")
@check_role_permissions("anti-hack")
async def anti_hack(interaction: discord.Interaction, actif: bool):
    config.CONFIG["security"]["anti_hack"] = actif
    await interaction.response.send_message(f"✅ Anti-hack {'activé' if actif else 'désactivé'}.", ephemeral=True)


# Commande utilitaire pour forcer la synchronisation des commandes sur le serveur courant
@bot.tree.command(name="sync", description="(Admin) Synchronise les commandes pour ce serveur")
@check_role_permissions("sync")
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
# === COMMANDES DE SAUVEGARDE ===
# ============================

@bot.tree.command(name="load-save", description="Charge TOUTE la configuration depuis un salon")
@discord.app_commands.describe(salon="Salon contenant la sauvegarde")
@check_role_permissions("load-save")
async def load_save(interaction: discord.Interaction, salon: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    loaded = None
    try:
        async for msg in salon.history(limit=100):
            for att in msg.attachments:
                if att.filename == "POUR_TOI.txt":
                    raw = await att.read()
                    import json
                    loaded = json.loads(raw.decode("utf-8"))
                    break
            if loaded:
                break
        if not loaded:
            await interaction.followup.send("❌ Fichier `POUR_TOI.txt` non trouvé.", ephemeral=True)
            return

        # === Mettre à jour TOUT ===
        config.CONFIG.clear()
        config.CONFIG.update(loaded)

        # === Recréer les salons de log si absents ===
        log_cat = None
        for cat in interaction.guild.categories:
            if "surveillances" in cat.name.lower():
                log_cat = cat
                break
        if not log_cat:
            overwrites = {interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False)}
            log_cat = await interaction.guild.create_category(
                name="𓆩𖤍𓆪۰⟣ SURVEILLANCES ⟢۰𓆩𖤍𓆪",
                overwrites=overwrites
            )
            for name in ["📜・messages", "🎤・vocal", "🎫・tickets", "👑・rôles", "🚨・alertes", "⚖️・sanctions", "🛠️・commandes", "📛・profil", "🔍・contenu", "💥・bavures", "🎉・giveaway"]:
                await interaction.guild.create_text_channel(name=name, category=log_cat, overwrites=overwrites)

        # === Synchroniser les ID de salons existants ===
        mapping = {
            "messages": "messages",
            "vocal": "vocal",
            "ticket": "tickets",
            "moderation": "rôles",
            "securite": "alertes",
            "sanctions": "sanctions",
            "commands": "commandes",
            "profile": "profil",
            "content": "contenu",
            "alerts": "alertes",
            "giveaway": "giveaway",
            "bavures": "bavures"
        }
        log_channels = {}
        for ch in log_cat.text_channels:
            for key, keyword in mapping.items():
                if keyword in ch.name.lower():
                    log_channels[key] = ch.id
        config.CONFIG.setdefault("logs", {}).update(log_channels)

        # === Sauvegarder en mémoire ===
        await save_guild_config(interaction.guild, config.CONFIG)

        await interaction.followup.send("✅ **Configuration complète chargée avec succès !**", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Erreur : {e}", ephemeral=True)

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