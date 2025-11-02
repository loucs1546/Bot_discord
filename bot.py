# test_bot.py
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

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
async def logs(interaction: discord.Interaction, type: str, salon: discord.TextChannel):
    await interaction.response.send_message(f"✅ Salon **{type}** défini sur {salon.mention}.", ephemeral=True)

@bot.tree.command(name="scan-deleted", description="Récupère les suppressions récentes")
@discord.app_commands.checks.has_permissions(administrator=True)
async def scan_deleted(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Commande de test fonctionnelle !", ephemeral=True)

@bot.event
async def on_ready():
    print(f"✅ {bot.user} est prêt !")
    guild = discord.Object(id=GUILD_ID)
    synced = await bot.tree.sync(guild=guild)
    print(f"✅ {len(synced)} commandes synchronisées : {[c.name for c in synced]}")

bot.run(TOKEN)