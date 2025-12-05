# cogs/security/antispam.py
import discord
from discord.ext import commands
import core_config as config
from utils.logging import send_log_to
from collections import defaultdict
import time
from config.filters import WHITELISTED_PHRASES

class AntiSpamCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.user_messages = defaultdict(list)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or message.guild.id != config.GUILD_ID:
            return

        # Désactivé par défaut → à activer via /anti-spam True
        if not config.CONFIG["security"]["anti_spam"]:
            return

        # Ignorer les messages longs (>30 caractères)
        if len(message.content) > 30:
            return

        clean_content = message.content.strip().lower()
        if clean_content in WHITELISTED_PHRASES:
            return

        uid = message.author.id
        now = time.time()
        self.user_messages[uid] = [t for t in self.user_messages[uid] if now - t[0] < 5]
        self.user_messages[uid].append((now, clean_content))

        if len(self.user_messages[uid]) >= 5:
            contents = [msg[1] for msg in self.user_messages[uid]]
            is_repetitive = len(set(contents)) == 1
            is_short = all(len(c) <= 5 for c in contents)

            if is_repetitive or is_short:
                try:
                    await message.delete()
                    embed = discord.Embed(
                        title="🗑️ Message supprimé (anti-spam)",
                        description=f"**Auteur** : {message.author.mention}\n**Salon** : {message.channel.mention}\n**Supprimé par** : {self.bot.user.mention} (bot)",
                        color=0xff0000,
                        timestamp=discord.utils.utcnow()
                    )
                    if message.content:
                        embed.add_field(name="Contenu", value=message.content[:1020], inline=False)
                    await send_log_to(self.bot, "securite", embed)
                    await message.channel.send(f"{message.author.mention}, veuillez ne pas spammer.", delete_after=5)
                except Exception:
                    pass

async def setup(bot):
    await bot.add_cog(AntiSpamCog(bot))