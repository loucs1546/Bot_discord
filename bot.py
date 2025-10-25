import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Vérifier que le token est bien chargé
if not TOKEN:
    raise ValueError("❌ DISCORD_TOKEN non trouvé. Vérifie ton fichier .env ou les Variables Railway.")

# Activer les intents nécessaires
intents = discord.Intents.default()
intents.message_content = True

# Créer le bot
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ {bot.user} est en ligne !")

# Fonction pour gérer les tickets
async def handle_ticket_embed(message):
    embed = message.embeds[0]
    guild = message.guild

    # Extraire les champs de l'embed
    fields = {field.name: field.value for field in embed.fields}
    full_name = fields.get("👤 Nom complet", "Inconnu")
    discord_tag = fields.get("💬 Discord", "Non spécifié")
    availability = fields.get("🕒 Disponibilité", "Non précisée")
    details = fields.get("📄 Détails", "Aucun détail fourni.")
    title = embed.title or "Ticket sans titre"
    reason = title.split(" : ", 1)[-1] if " : " in title else title

    # Nettoyer le pseudo Discord pour le nom du salon
    clean_tag = discord_tag.replace("#", "").replace("@", "").replace(" ", "-").lower()
    channel_name = f"ticket-{clean_tag}"

    # Trouver le rôle Staff (adapte le nom ici si nécessaire)
    staff_role = None
    possible_names = ["Staff", "Support", "Modérateur", "Mod", "staff", "support"]  # ajoute d'autres variantes si besoin
    for name in possible_names:
        role = discord.utils.get(guild.roles, name=name)
        if role:
            staff_role = role
            break

    if not staff_role:
        await message.channel.send("❌ Aucun rôle 'Staff' trouvé. Vérifie que le rôle existe sur ce serveur.")
        return

    # Configurer les permissions du nouveau salon
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        staff_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }

    # Créer le salon texte
    try:
        channel = await guild.create_text_channel(channel_name, overwrites=overwrites)

        # Envoyer le message d'accueil
        await channel.send(
            f"📩 **Nouveau ticket**\n"
            f"**Utilisateur :** {full_name} (`{discord_tag}`)\n"
            f"**Raison :** {reason}\n"
            f"**Disponibilité :** {availability}\n"
            f"**Détails :**\n{details}\n\n"
            f"🔔 Un membre du <@&{staff_role.id}> va vous répondre rapidement."
        )

        # Confirmation dans le salon source (optionnel)
        await message.channel.send(f"✅ Ticket créé : {channel.mention}")

    except Exception as e:
        await message.channel.send(f"❌ Erreur lors de la création du ticket : {e}")

# Écouter tous les messages
@bot.event
async def on_message(message):
    # Ignorer les messages du bot lui-même
    if message.author == bot.user:
        return

    # Réagir uniquement aux messages de ZentysBot avec un embed
    if message.author.name == "ZentysBot" and message.embeds:
        await handle_ticket_embed(message)

    # Ne pas oublier de traiter les commandes !
    await bot.process_commands(message)

# Commande manuelle pour fermer un ticket (optionnel mais utile)
@bot.command()
async def close(ctx):
    """Ferme le ticket actuel."""
    if "ticket-" in ctx.channel.name:
        await ctx.send("🔒 Ce ticket sera fermé dans 3 secondes...")
        await asyncio.sleep(3)
        await ctx.channel.delete()
    else:
        await ctx.send("❌ Cette commande ne fonctionne que dans un salon de ticket.")

# Lancer le bot
bot.run(TOKEN)
