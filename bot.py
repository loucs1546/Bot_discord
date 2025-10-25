import discord
from discord.ext import commands
from discord import app_commands  # ✅ Import ajouté
import os
import asyncio
import json
from dotenv import load_dotenv

# === CONFIGURATION ===
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("❌ DISCORD_TOKEN non trouvé. Vérifie les Variables Railway.")

ACTIVATED_FILE = "activated_channels.json"

def load_activated_channels():
    if os.path.exists(ACTIVATED_FILE):
        with open(ACTIVATED_FILE, "r", encoding="utf-8") as f:
            return {int(k): int(v) for k, v in json.load(f).items()}
    return {}

def save_activated_channels(data):
    with open(ACTIVATED_FILE, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in data.items()}, f)

activated_channels = load_activated_channels()

# === BOT SETUP ===
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# === COMMANDE : /active ===
@bot.tree.command(name="active", description="Active le système de tickets par webhook dans ce salon")
async def activate_webhook_tickets(interaction: discord.Interaction):
    staff_role = None
    for name in ["Staff", "Support", "Modérateur", "Mod", "staff", "support", "Équipe ZENTYS"]:
        role = discord.utils.get(interaction.guild.roles, name=name)
        if role and role in interaction.user.roles:
            staff_role = role
            break

    if not staff_role:
        await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
        return

    activated_channels[interaction.guild.id] = interaction.channel.id
    save_activated_channels(activated_channels)
    await interaction.response.send_message("✅ Système de tickets activé dans ce salon !", ephemeral=True)

# === COMMANDE : /ajout @Utilisateur ===
@bot.tree.command(name="ajout", description="Ajoute un membre au salon actuel")
@app_commands.describe(membre="Le membre à ajouter")
async def ajout(interaction: discord.Interaction, membre: discord.Member):
    if not any(role.name in ["Staff", "Support", "Modérateur", "Mod", "Équipe ZENTYS"] for role in interaction.user.roles):
        await interaction.response.send_message("❌ Tu n'as pas la permission.", ephemeral=True)
        return

    await interaction.channel.set_permissions(membre, read_messages=True, send_messages=True)
    await interaction.response.send_message(f"✅ {membre.mention} a été ajouté au salon.")

# === COMMANDE : /retire @Utilisateur ===
@bot.tree.command(name="retire", description="Retire un membre du salon actuel")
@app_commands.describe(membre="Le membre à retirer")
async def retire(interaction: discord.Interaction, membre: discord.Member):
    if not any(role.name in ["Staff", "Support", "Modérateur", "Mod", "Équipe ZENTYS"] for role in interaction.user.roles):
        await interaction.response.send_message("❌ Tu n'as pas la permission.", ephemeral=True)
        return

    await interaction.channel.set_permissions(membre, read_messages=False)
    await interaction.response.send_message(f"✅ {membre.mention} a été retiré du salon.")

# === COMMANDE : /messageoff @Utilisateur ===
@bot.tree.command(name="messageoff", description="Empêche un membre d'envoyer des messages dans ce salon")
@app_commands.describe(membre="Le membre à restreindre")
async def messageoff(interaction: discord.Interaction, membre: discord.Member):
    if not any(role.name in ["Staff", "Support", "Modérateur", "Mod", "Équipe ZENTYS"] for role in interaction.user.roles):
        await interaction.response.send_message("❌ Tu n'as pas la permission.", ephemeral=True)
        return

    await interaction.channel.set_permissions(membre, send_messages=False)
    await interaction.response.send_message(f"🔇 {membre.mention} ne peut plus envoyer de messages ici.")

# === COMMANDE : /urloff @Utilisateur ===
@bot.tree.command(name="urloff", description="Empêche un membre d'envoyer des liens/images dans ce salon")
@app_commands.describe(membre="Le membre à restreindre")
async def urloff(interaction: discord.Interaction, membre: discord.Member):
    if not any(role.name in ["Staff", "Support", "Modérateur", "Mod", "Équipe ZENTYS"] for role in interaction.user.roles):
        await interaction.response.send_message("❌ Tu n'as pas la permission.", ephemeral=True)
        return

    await interaction.channel.set_permissions(membre, attach_files=False, embed_links=False)
    await interaction.response.send_message(f"🔗 {membre.mention} ne peut plus envoyer de liens ou d'images ici.")

# === VUE DES BOUTONS DANS LE TICKET ===
class TicketView(discord.ui.View):
    def __init__(self, user_id, staff_role_id):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.staff_role_id = staff_role_id
        self.paused = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        is_staff = any(role.id == self.staff_role_id for role in interaction.user.roles)
        if interaction.data["custom_id"] in ["pause", "claim", "clear"] and not is_staff:
            await interaction.response.send_message("❌ Seul le staff peut utiliser ce bouton.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⏸️ Mettre en pause", style=discord.ButtonStyle.gray, custom_id="pause")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        channel = interaction.channel
        member = guild.get_member(self.user_id)

        if not member:
            await interaction.response.send_message("❌ Utilisateur introuvable.", ephemeral=True)
            return

        if self.paused:
            await channel.set_permissions(member, send_messages=True)
            button.label = "⏸️ Mettre en pause"
            button.style = discord.ButtonStyle.gray
            self.paused = False
        else:
            await channel.set_permissions(member, send_messages=False)
            button.label = "▶️ Reprendre"
            button.style = discord.ButtonStyle.green
            self.paused = True

        await interaction.response.edit_message(view=self)
        await channel.send(f"{'✅ Le ticket a été repris.' if not self.paused else '⏸️ Le ticket est en pause.'}")

    @discord.ui.button(label="👨‍💼 Prendre en charge", style=discord.ButtonStyle.blurple, custom_id="claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"👨‍💼 {interaction.user.mention} prend en charge ce ticket.")

    @discord.ui.button(label="🧹 Effacer les messages", style=discord.ButtonStyle.red, custom_id="clear")
    async def clear_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        def is_not_bot_msg(msg):
            return msg.id != interaction.message.id
        deleted = await interaction.channel.purge(limit=100, check=is_not_bot_msg)
        await interaction.followup.send(f"🧹 {len(deleted)} messages supprimés.", ephemeral=True)

    @discord.ui.button(label="🗑️ Fermer le ticket", style=discord.ButtonStyle.danger, custom_id="close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 Ce ticket sera fermé dans 3 secondes...")
        await asyncio.sleep(3)
        await interaction.channel.delete()

# === CRÉATION DU TICKET À PARTIR D'UN WEBHOOK ===
async def create_ticket_from_webhook(message):
    if not message.embeds:
        return

    embed = message.embeds[0]
    guild = message.guild

    fields = {field.name: field.value for field in embed.fields}
    full_name = fields.get("👤 Nom complet", "Inconnu")
    discord_tag = fields.get("💬 Discord", "Non spécifié")
    availability = fields.get("🕒 Disponibilité", "Non précisée")
    details = fields.get("📄 Détails", "Aucun détail fourni.")
    title = embed.title or "Ticket sans titre"
    reason = title.split(" : ", 1)[-1] if " : " in title else title

    clean_tag = discord_tag.replace("#", "").replace("@", "").replace(" ", "-").lower()
    channel_name = f"ticket-{clean_tag}"

    staff_role = None
    for name in ["Staff", "Support", "Modérateur", "Mod", "staff", "support", "Équipe ZENTYS"]:
        role = discord.utils.get(guild.roles, name=name)
        if role:
            staff_role = role
            break

    if not staff_role:
        await message.channel.send("❌ Rôle 'Staff' introuvable.")
        return

    member_to_add = None
    discord_tag_clean = discord_tag.strip()
    if discord_tag_clean.startswith('<@') and discord_tag_clean.endswith('>'):
        try:
            user_id = int(discord_tag_clean[2:-1].replace('!', ''))
            member_to_add = guild.get_member(user_id)
        except ValueError:
            pass
    if not member_to_add:
        for member in guild.members:
            if member.name == discord_tag_clean or str(member) == discord_tag_clean:
                member_to_add = member
                break

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        staff_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    if member_to_add:
        overwrites[member_to_add] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    try:
        channel = await guild.create_text_channel(channel_name, overwrites=overwrites)

        embed_response = discord.Embed(
            title="📩 Nouveau ticket",
            color=0x00ffff,
            timestamp=discord.utils.utcnow()
        )
        embed_response.add_field(name="👤 Nom complet", value=full_name, inline=True)
        embed_response.add_field(name="💬 Discord", value=discord_tag, inline=True)
        embed_response.add_field(name="🕒 Disponibilité", value=availability, inline=False)
        embed_response.add_field(name="📄 Détails", value=details, inline=False)
        embed_response.set_footer(text="ZENTYS - Système de tickets")
        embed_response.description = f"**Raison :** {reason}\n\n🔔 Un membre du <@&{staff_role.id}> va vous répondre rapidement."

        user_id = member_to_add.id if member_to_add else None
        view = TicketView(user_id=user_id, staff_role_id=staff_role.id)
        await channel.send(embed=embed_response, view=view)
        await message.channel.send(f"✅ Ticket créé : {channel.mention}")

    except Exception as e:
        await message.channel.send(f"❌ Erreur : {e}")

# === ÉCOUTE DES MESSAGES ===
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.webhook_id is not None:
        if message.guild.id in activated_channels:
            if message.channel.id == activated_channels[message.guild.id]:
                await create_ticket_from_webhook(message)
                return
    await bot.process_commands(message)

# === SYNCHRONISATION SUR TON SERVEUR (ID: 1084544847551148162) ===
@bot.event
async def on_ready():
    print(f"✅ {bot.user} est en ligne !")
    GUILD_ID = 1289495334069862452
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print("✅ Commandes slash synchronisées pour ton serveur.")

# === LANCEMENT ===
bot.run(TOKEN)
