import re
import discord
from discord.ext import commands
import core_config as config
from utils.logging import send_log_to

class ContentFilterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Liste des mots interdits (à remplacer par ta liste réelle)
        self.bad_words = [
            "merde", "pute", "connard", "salope", "enculé", "nique", "bite", "chienne"
        ]
        # Domaines autorisés (à ajuster)
        self.allowed_domains = [
            "discord.com",
            "discord.gg",
            "youtube.com",
            "youtu.be",
            "tenor.com",
            "giphy.com"
        ]

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

        # ✅ Désactiver si le salon est dans la liste
        disabled = config.CONFIG.get("content_filter", {}).get("disabled_channels", [])
        if message.channel.id in disabled:
            return

        # ✅ Ignorer si le message ne contient QUE des émojis
        cleaned = re.sub(r'<a?:\w+:\d+>', '', message.content)  # Émojis personnalisés
        cleaned = re.sub(r'[\U00010000-\U0010ffff\U00002600-\U000026ff]', '', cleaned)  # Émojis Unicode
        cleaned = re.sub(r'\s+', '', cleaned)
        if not cleaned:
            return

        # 🚫 Vérifier les gros mots
        content_lower = message.content.lower()
        for word in self.bad_words:
            if word in content_lower:
                await self._handle_suspicious_message(message, f"Gros mot détecté : `{word}`")
                return

        # 🚫 Vérifier les liens non autorisés
        url_pattern = re.compile(r'https?://[^\s]+')
        urls = url_pattern.findall(message.content)
        if urls:
            for url in urls:
                if not any(domain in url for domain in self.allowed_domains):
                    await self._handle_suspicious_message(message, f"Lien non autorisé : `{url}`")
                    return

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

        # ✅ Envoie le log dans le bon salon
        await send_log_to(self.bot, "content", embed)

        # ✅ Notification temporaire
        try:
            await message.channel.send(
                f"{message.author.mention}, votre message a été supprimé pour : **{reason}**.",
                delete_after=5
            )
        except:
            pass

async def setup(bot):
    await bot.add_cog(ContentFilterCog(bot))