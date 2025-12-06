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

# === VIEWS POUR TICKETS ===
class TicketChoiceSelect(discord.ui.Select):
    """Select menu pour choisir le type de ticket"""
    def __init__(self, guild: discord.Guild, ticket_system: str):
        systems = config.CONFIG.get("ticket_systems", {})
        sys_conf = systems.get(ticket_system, {})
        options_list = sys_conf.get("options", ["Support Général", "Bug Report", "Suggestion", "Autre"])
        select_options = [
            discord.SelectOption(label=opt[:100], value=opt) 
            for opt in options_list[:25]
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
    def __init__(self, guild: discord.Guild, ticket_system: str):
        super().__init__(timeout=300)
        self.guild = guild
        self.ticket_system = ticket_system
        self.selected_option = None
        
        # Ajouter le Select menu
        select = TicketChoiceSelect(guild, ticket_system)
        
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
        config.CONFIG.setdefault("ticket_systems", {}).setdefault(self.ticket_system, {}).setdefault("counter", 0)
        ticket_num = config.CONFIG["ticket_systems"][self.ticket_system]["counter"] + 1
        config.CONFIG["ticket_systems"][self.ticket_system]["counter"] = ticket_num
        
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
    def __init__(self, ticket_system=None):
        super().__init__(timeout=None)
        self.ticket_system = ticket_system
    @discord.ui.button(label="📩 Créer un ticket", style=discord.ButtonStyle.success, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user
        # Vérifier qu'il n'a pas déjà un ticket
        for channel in guild.channels:
            if channel.name.startswith("ticket-") and str(user.id) in channel.name:
                await interaction.response.send_message("Vous avez déjà un ticket ouvert !", ephemeral=True)
                return
        # Récupérer la config du système
        sys = self.ticket_system or "default"
        systems = config.CONFIG.get("ticket_systems", {})
        sys_conf = systems.get(sys)
        if not sys_conf:
            await interaction.response.send_message("❌ Système de ticket introuvable.", ephemeral=True)
            return
        mode = sys_conf.get("mode", "basic")
        options = sys_conf.get("options", ["Support Général"])
        counter = sys_conf.get("counter", 0)
        if mode == "basic":
            selected_option = options[0] if options else "Support Général"
            ticket_num = counter + 1
            config.CONFIG["ticket_systems"][sys]["counter"] = ticket_num
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
            embed = discord.Embed(
                title=f"🎟️ {selected_option} - #{ticket_num:06d}",
                description=f"Bonjour {user.mention},\n\n📝 Décrivez votre demande en détail. Un membre de l'équipe vous répondra bientôt.\n\n> ⚠️ Les fichiers et liens ne sont pas autorisés dans les tickets.",
                color=0x5865F2,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text="Seiko Security • Système de tickets")
            view = TicketManagementView(user.id, ticket_num)
            await ticket_channel.send(embed=embed, view=view)
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
        await interaction.response.send_message(embed=embed, view=TicketChoiceView(guild, sys), ephemeral=True)

class TicketChoiceView(discord.ui.View):
    def __init__(self, guild: discord.Guild, ticket_system: str):
        super().__init__(timeout=300)
        self.guild = guild
        self.ticket_system = ticket_system
        self.selected_option = None
        select = TicketChoiceSelect(guild, ticket_system)
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
        # Récupérer la config du système
        sys = self.ticket_system or "default"
        systems = config.CONFIG.get("ticket_systems", {})
        sys_conf = systems.get(sys)
        if not sys_conf:
            await interaction.response.send_message("❌ Système de ticket introuvable.", ephemeral=True)
            return
        ticket_num = sys_conf.get("counter", 0) + 1
        config.CONFIG["ticket_systems"][sys]["counter"] = ticket_num
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
        embed = discord.Embed(
            title=f"🎟️ {self.selected_option} - #{ticket_num:06d}",
            description=f"Bonjour {user.mention},\n\n📝 Décrivez votre demande en détail. Un membre de l'équipe vous répondra bientôt.\n\n> ⚠️ Les fichiers et liens ne sont pas autorisés dans les tickets.",
            color=0x5865F2,
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="Seiko Security • Système de tickets")
        view = TicketManagementView(user.id, ticket_num)
        await ticket_channel.send(embed=embed, view=view)
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


# === EVENT: on_ready ===
@bot.event
async def on_ready():
    global cogs_loaded
    if cogs_loaded:
        return
    print(f"✅ {bot.user} est en ligne !")
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
# === COMMANDES DE LOGS ===
# ============================

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
# === COMMANDES DE TICKETS MULTI-PANEL ===
# ============================

@bot.tree.command(name="ticket-config", description="Configurer le système de tickets")
@discord.app_commands.checks.has_permissions(administrator=True)
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

class TicketOptionsModal(discord.ui.Modal, title="Options personnalisées"):
    option1 = discord.ui.TextInput(label="Option 1", placeholder="Support VIP", max_length=100)
    option2 = discord.ui.TextInput(label="Option 2 (optionnel)", required=False, max_length=100)
    option3 = discord.ui.TextInput(label="Option 3 (optionnel)", required=False, max_length=100)
    def __init__(self, sys_name):
        super().__init__()
        self.sys_name = sys_name
    async def on_submit(self, interaction: discord.Interaction):
        opts = [self.option1.value.strip()]
        if self.option2.value: opts.append(self.option2.value.strip())
        if self.option3.value: opts.append(self.option3.value.strip())
        config.CONFIG.setdefault("ticket_systems", {}).setdefault(self.sys_name, {})
        config.CONFIG["ticket_systems"][self.sys_name]["mode"] = "advanced"
        config.CONFIG["ticket_systems"][self.sys_name]["options"] = [o for o in opts if o]
        await interaction.response.send_message(
            f"✅ Mode avancé configuré pour **{self.sys_name}** :\n" + "\n".join(f"• {o}" for o in opts if o),
            ephemeral=True
        )
        try:
            await save_guild_config(interaction.guild, config.CONFIG)
        except Exception:
            pass

@bot.tree.command(name="ticket-panel", description="Envoie le panneau de création de ticket")
@discord.app_commands.checks.has_permissions(administrator=True)
async def ticket_panel(interaction: discord.Interaction):
    systems = config.CONFIG.get("ticket_systems", {})
    if not systems:
        await interaction.response.send_message("❌ Aucun système de ticket configuré. Utilisez `/ticket-config`.", ephemeral=True)
        return
    embed = discord.Embed(
        title="🎟️ Support - Choisissez un système de ticket",
        description="Sélectionnez le système de ticket à utiliser.",
        color=0x2f3136,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Seiko Security • Système multi-tickets")
    await interaction.channel.send(embed=embed, view=TicketPanelMultiView(systems))
    await interaction.response.send_message("✅ Panel multi-tickets envoyé.", ephemeral=True)

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

# TicketView adapté pour multi-systèmes
class TicketView(discord.ui.View):
    def __init__(self, ticket_system=None):
        super().__init__(timeout=None)
        self.ticket_system = ticket_system
    @discord.ui.button(label="📩 Créer un ticket", style=discord.ButtonStyle.success, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user
        # Vérifier qu'il n'a pas déjà un ticket
        for channel in guild.channels:
            if channel.name.startswith("ticket-") and str(user.id) in channel.name:
                await interaction.response.send_message("Vous avez déjà un ticket ouvert !", ephemeral=True)
                return
        # Récupérer la config du système
        sys = self.ticket_system or "default"
        systems = config.CONFIG.get("ticket_systems", {})
        sys_conf = systems.get(sys)
        if not sys_conf:
            await interaction.response.send_message("❌ Système de ticket introuvable.", ephemeral=True)
            return
        mode = sys_conf.get("mode", "basic")
        options = sys_conf.get("options", ["Support Général"])
        counter = sys_conf.get("counter", 0)
        if mode == "basic":
            selected_option = options[0] if options else "Support Général"
            ticket_num = counter + 1
            config.CONFIG["ticket_systems"][sys]["counter"] = ticket_num
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
            embed = discord.Embed(
                title=f"🎟️ {selected_option} - #{ticket_num:06d}",
                description=f"Bonjour {user.mention},\n\n📝 Décrivez votre demande en détail. Un membre de l'équipe vous répondra bientôt.\n\n> ⚠️ Les fichiers et liens ne sont pas autorisés dans les tickets.",
                color=0x5865F2,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text="Seiko Security • Système de tickets")
            view = TicketManagementView(user.id, ticket_num)
            await ticket_channel.send(embed=embed, view=view)
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
        await interaction.response.send_message(embed=embed, view=TicketChoiceView(guild, sys), ephemeral=True)

class TicketChoiceView(discord.ui.View):
    def __init__(self, guild: discord.Guild, ticket_system: str):
        super().__init__(timeout=300)
        self.guild = guild
        self.ticket_system = ticket_system
        self.selected_option = None
        select = TicketChoiceSelect(guild, ticket_system)
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
        # Récupérer la config du système
        sys = self.ticket_system or "default"
        systems = config.CONFIG.get("ticket_systems", {})
        sys_conf = systems.get(sys)
        if not sys_conf:
            await interaction.response.send_message("❌ Système de ticket introuvable.", ephemeral=True)
            return
        ticket_num = sys_conf.get("counter", 0) + 1
        config.CONFIG["ticket_systems"][sys]["counter"] = ticket_num
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
        embed = discord.Embed(
            title=f"🎟️ {self.selected_option} - #{ticket_num:06d}",
            description=f"Bonjour {user.mention},\n\n📝 Décrivez votre demande en détail. Un membre de l'équipe vous répondra bientôt.\n\n> ⚠️ Les fichiers et liens ne sont pas autorisés dans les tickets.",
            color=0x5865F2,
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="Seiko Security • Système de tickets")
        view = TicketManagementView(user.id, ticket_num)
        await ticket_channel.send(embed=embed, view=view)
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
# === COMMANDES DE TICKETS MULTI-PANEL ===
# ============================

@bot.tree.command(name="ticket-config", description="Configurer le système de tickets")
@discord.app_commands.checks.has_permissions(administrator=True)
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

class TicketOptionsModal(discord.ui.Modal, title="Options personnalisées"):
    option1 = discord.ui.TextInput(label="Option 1", placeholder="Support VIP", max_length=100)
    option2 = discord.ui.TextInput(label="Option 2 (optionnel)", required=False, max_length=100)
    option3 = discord.ui.TextInput(label="Option 3 (optionnel)", required=False, max_length=100)
    def __init__(self, sys_name):
        super().__init__()
        self.sys_name = sys_name
    async def on_submit(self, interaction: discord.Interaction):
        opts = [self.option1.value.strip()]
        if self.option2.value: opts.append(self.option2.value.strip())
        if self.option3.value: opts.append(self.option3.value.strip())
        config.CONFIG.setdefault("ticket_systems", {}).setdefault(self.sys_name, {})
        config.CONFIG["ticket_systems"][self.sys_name]["mode"] = "advanced"
        config.CONFIG["ticket_systems"][self.sys_name]["options"] = [o for o in opts if o]
        await interaction.response.send_message(
            f"✅ Mode avancé configuré pour **{self.sys_name}** :\n" + "\n".join(f"• {o}" for o in opts if o),
            ephemeral=True
        )
        try:
            await save_guild_config(interaction.guild, config.CONFIG)
        except Exception:
            pass

@bot.tree.command(name="ticket-panel", description="Envoie le panneau de création de ticket")
@discord.app_commands.checks.has_permissions(administrator=True)
async def ticket_panel(interaction: discord.Interaction):
    systems = config.CONFIG.get("ticket_systems", {})
    if not systems:
        await interaction.response.send_message("❌ Aucun système de ticket configuré. Utilisez `/ticket-config`.", ephemeral=True)
        return
    embed = discord.Embed(
        title="🎟️ Support - Choisissez un système de ticket",
        description="Sélectionnez le système de ticket à utiliser.",
        color=0x2f3136,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Seiko Security • Système multi-tickets")
    await interaction.channel.send(embed=embed, view=TicketPanelMultiView(systems))
    await interaction.response.send_message("✅ Panel multi-tickets envoyé.", ephemeral=True)

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

# TicketView adapté pour multi-systèmes
class TicketView(discord.ui.View):
    def __init__(self, ticket_system=None):
        super().__init__(timeout=None)
        self.ticket_system = ticket_system
    @discord.ui.button(label="📩 Créer un ticket", style=discord.ButtonStyle.success, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user
        # Vérifier qu'il n'a pas déjà un ticket
        for channel in guild.channels:
            if channel.name.startswith("ticket-") and str(user.id) in channel.name:
                await interaction.response.send_message("Vous avez déjà un ticket ouvert !", ephemeral=True)
                return
        # Récupérer la config du système
        sys = self.ticket_system or "default"
        systems = config.CONFIG.get("ticket_systems", {})
        sys_conf = systems.get(sys)
        if not sys_conf:
            await interaction.response.send_message("❌ Système de ticket introuvable.", ephemeral=True)
            return
        mode = sys_conf.get("mode", "basic")
        options = sys_conf.get("options", ["Support Général"])
        counter = sys_conf.get("counter", 0)
        if mode == "basic":
            selected_option = options[0] if options else "Support Général"
            ticket_num = counter + 1
            config.CONFIG["ticket_systems"][sys]["counter"] = ticket_num
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
            embed = discord.Embed(
                title=f"🎟️ {selected_option} - #{ticket_num:06d}",
                description=f"Bonjour {user.mention},\n\n📝 Décrivez votre demande en détail. Un membre de l'équipe vous répondra bientôt.\n\n> ⚠️ Les fichiers et liens ne sont pas autorisés dans les tickets.",
                color=0x5865F2,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text="Seiko Security • Système de tickets")
            view = TicketManagementView(user.id, ticket_num)
            await ticket_channel.send(embed=embed, view=view)
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
        await interaction.response.send_message(embed=embed, view=TicketChoiceView(guild, sys), ephemeral=True)

class TicketChoiceView(discord.ui.View):
    def __init__(self, guild: discord.Guild, ticket_system: str):
        super().__init__(timeout=300)
        self.guild = guild
        self.ticket_system = ticket_system
        self.selected_option = None
        select = TicketChoiceSelect(guild, ticket_system)
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
        # Récupérer la config du système
        sys = self.ticket_system or "default"
        systems = config.CONFIG.get("ticket_systems", {})
        sys_conf = systems.get(sys)
        if not sys_conf:
            await interaction.response.send_message("❌ Système de ticket introuvable.", ephemeral=True)
            return
        ticket_num = sys_conf.get("counter", 0) + 1
        config.CONFIG["ticket_systems"][sys]["counter"] = ticket_num
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
        embed = discord.Embed(
            title=f"🎟️ {self.selected_option} - #{ticket_num:06d}",
            description=f"Bonjour {user.mention},\n\n📝 Décrivez votre demande en détail. Un membre de l'équipe vous répondra bientôt.\n\n> ⚠️ Les fichiers et liens ne sont pas autorisés dans les tickets.",
            color=0x5865F2,
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="Seiko Security • Système de tickets")
        view = TicketManagementView(user.id, ticket_num)
        await ticket_channel.send(embed=embed, view=view)
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
# === COMMANDES DE LOGS ===
# ============================

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
# === COMMANDES DE SAUVEGARDE ===
# ============================

@bot.tree.command(name="load-save", description="Charge une sauvegarde depuis un salon de sauvegarde")
@discord.app_commands.describe(salon="Salon contenant la sauvegarde (choix via autocomplete)")
@discord.app_commands.checks.has_permissions(administrator=True)
async def load_save(interaction: discord.Interaction, salon: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    loaded_config = None
    source_msg = None
    try:
        async for msg in salon.history(limit=300):
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
            if msg.content and ("{" in msg.content and "}" in msg.content):
                try:
                    import json, re
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

        # Nouvelle logique: ne pas créer de salons, mais mapper les logs si existants
        log_keys = [
            "messages", "moderation", "ticket", "vocal", "securite",
            "commands", "profile", "content", "alerts", "sanctions", "giveaway", "bavures"
        ]
        found_channels = {}
        for category in guild.categories:
            if "log" in category.name.lower() or "surveillance" in category.name.lower():
                for channel in category.text_channels:
                    for key in log_keys:
                        if key in channel.name.lower() or key in channel.name:
                            found_channels[key] = channel.id
        missing = [k for k in log_keys if k not in found_channels]
        if missing:
            await interaction.followup.send(
                "❌ Les salons de logs suivants sont manquants :\n" +
                "\n".join(f"• {k}" for k in missing) +
                "\n\nVeuillez utiliser `/add-cat-log` pour créer le système de logs avant de charger la sauvegarde.",
                ephemeral=True
            )
            return
        # Mettre à jour la config avec les salons trouvés
        loaded_config.setdefault("logs", {}).update(found_channels)
        config.CONFIG.update(loaded_config)
        try:
            await save_guild_config(guild, config.CONFIG)
        except Exception:
            pass
        keys = ", ".join(sorted(list(loaded_config.keys()))) if isinstance(loaded_config, dict) else "données non structurées"
        summary = (
            f"✅ Sauvegarde chargée depuis {salon.mention}.\n"
            f"🔎 Message source : <@{source_msg.author.id}> (ID: {source_msg.id})\n"
            f"🗂️ Clés détectées : {keys}\n"
            f"💾 Configuration mise à jour en mémoire et sauvegardée."
        )
        await interaction.followup.send(summary, ephemeral=True)
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