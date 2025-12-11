import re
import discord
from discord.ext import commands
import core_config as config
from utils.logging import send_log_to

class ContentFilterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignorer les messages du bot, MP, ou hors serveur configuré
        if (
            message.author == self.bot.user or
            not message.guild or
            message.guild.id != config.GUILD_ID or
            message.content.strip() == ""
        ):
            return

        # ✅ 1. Si le salon est désactivé → ignorer
        disabled = config.CONFIG.get("content_filter", {}).get("disabled_channels", [])
        if message.channel.id in disabled:
            return

        # ✅ 2. Si le message ne contient QUE des émojis (ou rien d'autre) → autoriser
        #     → Supprime les émojis Unicode et les émojis personnalisés <a:...:123>
        cleaned = re.sub(r'<a?:\w+:\d+>', '', message.content)  # émojis personnalisés
        cleaned = re.sub(r'[\U00010000-\U0010ffff\U00002600-\U000026ff]', '', cleaned)  # émojis Unicode
        cleaned = re.sub(r'\s+', '', cleaned)  # espaces

        if not cleaned:
            return  # ✅ Seulement des émojis → OK

        # ✅ 3. Vérifier gros mots, liens, etc. (logique existante)
        # ... (garde ta logique actuelle de détection ici)
        # Exemple :
        bad_words = ["merde", "connard", " salope"]  # à remplacer par ta liste
        if any(word in message.content.lower() for word in bad_words):
            await self._handle_suspicious_message(message, "Gros mot détecté")

        # Détecter les liens non autorisés (si tu en as)
        url_pattern = re.compile(r'https?://[^\s]+')
        if url_pattern.search(message.content):
            allowed_domains = ["discord.com", "youtube.com"]  # exemple
            if not any(domain in message.content for domain in allowed_domains):
                await self._handle_suspicious_message(message, "Lien non autorisé")

    async def _handle_suspicious_message(self, message, reason: str):
        try:
            await message.delete()
        except:
            pass

        embed = discord.Embed(
            title="⚠️ Contenu suspect supprimé",
            description=f"**Auteur** : {message.author.mention}\n**Salon** : {message.channel.mention}\n**Raison** : {reason}",
            color=0xff6600,
            timestamp=message.created_at
        )
        if message.content:
            embed.add_field(name="Contenu", value=message.content[:1000], inline=False)

        await send_log_to(self.bot, "content", embed)

        try:
            await message.channel.send(
                f"{message.author.mention}, votre message a été supprimé pour : **{reason}**.",
                delete_after=5
            )
        except:
            pass

async def setup(bot):
    await bot.add_cog(ContentFilterCog(bot))