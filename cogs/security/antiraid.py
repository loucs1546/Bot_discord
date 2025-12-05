# cogs/security/antiraid.py
import discord
from discord.ext import commands
import core_config as config
from utils.logging import send_log_to  # ← send_log_to, pas send_log
import time

class AntiRaidCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.join_log = []
        self.invite_tracker = {}

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.guild.id != config.GUILD_ID:
            return
        now = time.time()
        self.join_log = [entry for entry in self.join_log if now - entry[0] < 60]
        invite_code = None
        try:
            invites = await member.guild.invites()
            for invite in invites:
                if invite.uses > 0 and invite.inviter:
                    if invite.code not in self.invite_tracker:
                        self.invite_tracker[invite.code] = []
                    self.invite_tracker[invite.code].append(member.id)
                    invite_code = invite.code
                    break
        except:
            pass
        self.join_log.append((now, member, invite_code))
        if len(self.join_log) >= 5:
            if invite_code:
                same_invite_count = len(self.invite_tracker.get(invite_code, []))
                if same_invite_count >= 3:
                    embed = discord.Embed(
                        title="🚨 RAID SUSPECT DÉTECTÉ",
                        description=f"{len(self.join_log)} membres en 60 secondes.\nInvitation commune : `{invite_code}`\nCréateur : {invite.inviter.mention if 'invite' in locals() else 'Inconnu'}",
                        color=0xff0000,
                        timestamp=discord.utils.utcnow()
                    )
                    await send_log_to(self.bot, "alerts", embed)  # ← send_log_to

async def setup(bot):
    await bot.add_cog(AntiRaidCog(bot))