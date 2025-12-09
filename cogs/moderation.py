# cogs/moderation.py
import discord
from discord.ext import commands
import core_config as config
from utils.logging import send_log_to
import time

class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.settings = {}
        self.user_messages = {}
        self.join_log = []

    def get_settings(self, guild_id):
        if guild_id not in self.settings:
            self.settings[guild_id] = {
                "anti_spam": False,
                "anti_raid": False,
                "anti_hack": False
            }
        return self.settings[guild_id]

    @discord.app_commands.command(name="anti-spam", description="Active/désactive l'anti-spam")
    @check_role_permissions("anti_spam")
    async def anti_spam(self, interaction: discord.Interaction, actif: bool):
        settings = self.get_settings(interaction.guild.id)
        settings["anti_spam"] = actif
        await interaction.response.send_message(f"✅ Anti-spam {'activé' if actif else 'désactivé'}.", ephemeral=True)

    @discord.app_commands.command(name="anti-raid", description="Active/désactive l'anti-raid")
    @check_role_permissions("anti_raid")
    async def anti_raid(self, interaction: discord.Interaction, actif: bool):
        settings = self.get_settings(interaction.guild.id)
        settings["anti_raid"] = actif
        await interaction.response.send_message(f"✅ Anti-raid {'activé' if actif else 'désactivé'}.", ephemeral=True)

    @discord.app_commands.command(name="anti-hack", description="Active/désactive l'anti-hack")
    @check_role_permissions("anti_hack")
    async def anti_hack(self, interaction: discord.Interaction, actif: bool):
        settings = self.get_settings(interaction.guild.id)
        settings["anti_hack"] = actif
        await interaction.response.send_message(f"✅ Anti-hack {'activé' if actif else 'désactivé'}.", ephemeral=True)

    # === ANTI-SPAM ===
    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore les messages du bot lui-même pour éviter les boucles
        if message.author.id == self.bot.user.id or not message.guild:
            return

        # Vérifie si le message vient du salon "sanctions"
        sanctions_channel_id = config.CONFIG["logs"].get("sanctions")
        if not sanctions_channel_id or message.channel.id != sanctions_channel_id:
            return

        # Vérifie si c'est un embed de sanction
        if message.embeds:
            embed = message.embeds[0]
            if embed.description and "Raison : " in embed.description:
                # Extrait la raison
                lines = embed.description.split("\n")
                raison_line = next((line for line in lines if line.startswith("**Raison** : ")), None)
                if raison_line:
                    # "Raison : ..." → on prend ce qui est après les ": "
                    raison = raison_line.split(": ", 1)[1].strip()

                    # Vérifie si la raison est invalide
                    if not raison or len(raison.strip()) == 0 or (len(raison.strip()) == 1 and raison.strip().isalpha()):
                        # Envoie dans "bavures"
                        bavures_ch = self.bot.get_channel(config.CONFIG["logs"].get("bavures"))
                        if bavures_ch:
                            await bavures_ch.send(embed=embed)

    # === ANTI-RAID & ANTI-HACK ===
    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.guild.id != config.GUILD_ID:
            return

        settings = self.get_settings(member.guild.id)
        now = time.time()

        # ANTI-RAID
        if settings["anti_raid"]:
            self.join_log = [t for t in self.join_log if now - t < 10]
            self.join_log.append(now)
            if len(self.join_log) > 10:
                embed = discord.Embed(
                    title="🚨 RAID DÉTECTÉ",
                    description=f"{len(self.join_log)} membres en 10 secondes.",
                    color=0xff0000,
                    timestamp=discord.utils.utcnow()
                )
                await send_log_to(self.bot, "threats", embed)

        # ANTI-HACK
        if settings["anti_hack"]:
            created_ago = (discord.utils.utcnow() - member.created_at).total_seconds()
            suspicious_name = len(member.name) < 3 or len(member.name) > 15 or not member.name.isalnum()
            if created_ago < 300 and member.avatar is None and suspicious_name:
                try:
                    await member.kick(reason="Compte suspect (anti-hack)")
                    embed = discord.Embed(
                        title="🛡️ Compte expulsé (anti-hack)",
                        description=f"{member.mention} (`{member.id}`)",
                        color=0x9b59b6,
                        timestamp=discord.utils.utcnow()
                    )
                    await send_log_to(self.bot, "threats", embed)
                except Exception:
                    pass

async def setup(bot):
    await bot.add_cog(ModerationCog(bot))