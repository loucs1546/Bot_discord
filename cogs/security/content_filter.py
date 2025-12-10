# cogs/security/content_filter.py
import discord
from discord.ext import commands
import core_config as config
from config.filters import est_contenu_suspect
from utils.logging import send_log_to
from utils.views import ContentReviewView

class ContentFilterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or message.guild.id != config.GUILD_ID:
            return
        # Always block attachments in ticket channels
        if message.channel and getattr(message.channel, 'name', '').startswith("ticket-"):
            if message.attachments:
                try:
                    await message.delete()
                    await message.channel.send(f"{message.author.mention} Les fichiers sont interdits dans les tickets.", delete_after=5)
                except:
                    pass
                return

        if est_contenu_suspect(message.content):
            try:
                await message.delete()
            except:
                pass

            # Crée un embed ou un message à logger
            embed = discord.Embed(
                title="🔍 Contenu filtré",
                description=f"**Auteur** : {message.author.mention}\n**Salon** : {message.channel.mention}",
                color=0xff6600,
                timestamp=message.created_at
            )
            if message.content:
                embed.add_field(name="Contenu", value=message.content[:1000], inline=False)

            await send_log_to(self.bot, "content", embed)  # ← embed = content

async def setup(bot):
    await bot.add_cog(ContentFilterCog(bot))