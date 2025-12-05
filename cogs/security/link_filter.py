# cogs/security/link_filter.py
import discord
from discord.ext import commands
import core_config as config
import re
from utils.logging import send_log_to
from config.filters import est_url_suspecte
from utils.views import ContentReviewView

URL_REGEX = re.compile(r"https?://[^\s]+")

class LinkFilterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or message.guild.id != config.GUILD_ID:
            return

        urls = URL_REGEX.findall(message.content)
        if not urls:
            return
        # If it's a ticket channel, always remove links
        if message.channel and getattr(message.channel, 'name', '').startswith("ticket-"):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention} Les liens sont interdits dans les tickets.", delete_after=5)
            except:
                pass
            return

        try:
            await message.delete()
        except:
            pass

        embed = discord.Embed(
            title="🗑️ Message supprimé (lien suspect)",
            description=f"**Auteur** : {message.author.mention}\n**Salon** : {message.channel.mention}\n**Supprimé par** : {self.bot.user.mention} (bot)",
            color=0xff6600,
            timestamp=discord.utils.utcnow()
        )
        if message.content:
            embed.add_field(name="Contenu", value=message.content[:1020], inline=False)

        view = ContentReviewView(message.content, message.author, message.channel, self.bot)
        await send_log_to(self.bot, "content", embed)
        await send_log_to(self.bot, "content", view=view)

async def setup(bot):
    await bot.add_cog(LinkFilterCog(bot))