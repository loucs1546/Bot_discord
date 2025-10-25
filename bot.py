import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import sys

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    print("❌ ERREUR : Token non trouvé. Vérifie ton fichier .env !")
    sys.exit(1)

print(f"🔍 Token chargé (début) : {TOKEN[:10]}...")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

async def handle_ticket_embed(message):
    embed = message.embeds[0]  # On prend le premier embed

    # Récupérer le titre (ex: "Nouveau ticket : Blanchiment d'argent")
    title = embed.title or "Ticket sans titre"

    # Extraire les champs (Nom complet, Disponibilité, Détails...)
    fields = {}
    for field in embed.fields:
        fields[field.name] = field.value

    # Récupérer le nom complet (si présent)
    full_name = fields.get("Nom complet", "Inconnu")

    # Récupérer le pseudo Discord (si présent)
    discord_tag = fields.get("Discord", "Non spécifié")

    # Récupérer la disponibilité
    availability = fields.get("Disponibilité", "Non spécifiée")

    # Récupérer les détails
    details = fields.get("Détails", "Aucun détail fourni.")

    # Créer le nom du salon : ticket_pseudo_discord
    channel_name = f"ticket_{discord_tag.replace('#', '').replace(' ', '-').lower()}"

    # Récupérer le serveur
    guild = message.guild

    # Définir les permissions : visible seulement pour le staff + bot
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }

    # Ajouter le rôle Staff (à adapter selon ton serveur)
    staff_role = discord.utils.get(guild.roles, name="Staff")  # Change "Staff" par le nom réel de ton rôle
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    try:
        # Créer le salon
        channel = await guild.create_text_channel(channel_name, overwrites=overwrites)

        # Envoyer le message d'accueil
        await channel.send(
            f"📩 **Nouveau ticket**\n"
            f"**Utilisateur :** {full_name} ({discord_tag})\n"
            f"**Raison :** {title.split(': ')[-1] if ': ' in title else title}\n"
            f"**Disponibilité :** {availability}\n"
            f"**Détails :**\n{details}\n\n"
            f"🔔 Un membre du <@&{staff_role.id}> va vous répondre rapidement."
        )

        # Optionnel : envoyer un message dans le salon original pour confirmer
        await message.channel.send(f"✅ Ticket créé : {channel.mention}")

    except Exception as e:
        await message.channel.send(f"❌ Erreur lors de la création du ticket : {e}")

@bot.event
async def on_message(message):
    # Ignore les messages du bot lui-même
    if message.author == bot.user:
        return

    # Vérifie si le message vient de ZentysBot (ou un autre bot spécifique)
    if message.author.name == "ZentysBot" and message.embeds:
        # On traite uniquement les messages avec embeds (comme sur ton screenshot)
        await handle_ticket_embed(message)

    # Ne pas oublier de traiter les commandes !
    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f"✅ {bot.user} est en ligne !")

@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong !")

try:
    bot.run(TOKEN)
except discord.LoginFailure:
    print("❌ ERREUR : Token invalide. Vérifie qu’il est correct et non expiré.")
except Exception as e:
    print(f"💥 Erreur inattendue : {e}")