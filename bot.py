import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("❌ DISCORD_TOKEN non trouvé.")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # nécessaire pour chercher les membres

bot = commands.Bot(command_prefix="!", intents=intents)

# ===== VUE INTERACTIVE AVEC BOUTONS =====
class TicketView(discord.ui.View):
    def __init__(self, user_id, staff_role_id):
        super().__init__(timeout=None)  # Ne pas expirer
        self.user_id = user_id
        self.staff_role_id = staff_role_id
        self.paused = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Vérifie si l'utilisateur est staff (pour certains boutons)
        is_staff = any(role.id == self.staff_role_id for role in interaction.user.roles)
        button_id = interaction.data["custom_id"]

        if button_id in ["pause", "claim", "clear"]:
            if not is_staff:
                await interaction.response.send_message("❌ Seul le staff peut utiliser ce bouton.", ephemeral=True)
                return False
        return True

    @discord.ui.button(label="⏸️ Mettre en pause", style=discord.ButtonStyle.gray, custom_id="pause")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        channel = interaction.channel
        member = guild.get_member(self.user_id)

        if self.paused:
            # Reprendre
            if member:
                await channel.set_permissions(member, send_messages=True)
            button.label = "⏸️ Mettre en pause"
            button.style = discord.ButtonStyle.gray
            self.paused = False
            await interaction.response.edit_message(view=self)
            await channel.send("✅ Le ticket a été **repris**.")
        else:
            # Mettre en pause
            if member:
                await channel.set_permissions(member, send_messages=False)
            button.label = "▶️ Reprendre"
            button.style = discord.ButtonStyle.green
            self.paused = True
            await interaction.response.edit_message(view=self)
            await channel.send("⏸️ Le ticket est **en pause**. L'utilisateur ne peut plus envoyer de messages.")

    @discord.ui.button(label="👨‍💼 Prendre en charge", style=discord.ButtonStyle.blurple, custom_id="claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"👨‍💼 {interaction.user.mention} prend en charge ce ticket.", ephemeral=False)

    @discord.ui.button(label="🧹 Effacer les messages", style=discord.ButtonStyle.red, custom_id="clear")
    async def clear_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        # Supprime tous les messages sauf le premier (celui du bot)
        def is_not_bot_msg(msg):
            return msg.id != interaction.message.id
        deleted = await interaction.channel.purge(limit=100, check=is_not_bot_msg)
        await interaction.followup.send(f"🧹 {len(deleted)} messages supprimés.", ephemeral=True)

    @discord.ui.button(label="🗑️ Fermer le ticket", style=discord.ButtonStyle.danger, custom_id="close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 Ce ticket sera fermé dans 3 secondes...", ephemeral=False)
        await asyncio.sleep(3)
        await interaction.channel.delete()

# ===== FONCTION DE CRÉATION DE TICKET =====
async def handle_ticket_embed(message):
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

    # Trouver le rôle Staff
    staff_role = None
    for name in ["Staff", "Support", "Modérateur", "Mod", "staff", "support", "Équipe ZENTYS"]:
        role = discord.utils.get(guild.roles, name=name)
        if role:
            staff_role = role
            break

    if not staff_role:
        await message.channel.send("❌ Rôle 'Staff' introuvable.")
        return

    # Chercher l'utilisateur dans le serveur
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

    # Permissions du salon
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        staff_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    if member_to_add:
        overwrites[member_to_add] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    try:
        channel = await guild.create_text_channel(channel_name, overwrites=overwrites)

        # Embed stylé
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

        # Envoyer le message avec les boutons
        user_id = member_to_add.id if member_to_add else None
        view = TicketView(user_id=user_id, staff_role_id=staff_role.id)
        await channel.send(embed=embed_response, view=view)

        await message.channel.send(f"✅ Ticket créé : {channel.mention}")

    except Exception as e:
        await message.channel.send(f"❌ Erreur : {e}")

# ===== ÉCOUTE DES MESSAGES =====
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.author.name == "ZentysBot" and message.embeds:
        await handle_ticket_embed(message)
    await bot.process_commands(message)

# ===== COMMANDE MANUELLE (optionnelle) =====
@bot.command()
async def close(ctx):
    if "ticket-" in ctx.channel.name:
        await ctx.send("🔒 Ce ticket sera fermé dans 3 secondes...")
        await asyncio.sleep(3)
        await ctx.channel.delete()
    else:
        await ctx.send("❌ Commande réservée aux salons de ticket.")

# ===== LANCEMENT =====
bot.run(TOKEN)
