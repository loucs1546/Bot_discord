import discord
from discord.ext import commands

class TicketCog(commands.Cog):
    @discord.app_commands.command(name="active", description="Active les webhooks de tickets")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def active(self, interaction: discord.Interaction):
        await interaction.response.send_message("✅ Webhooks de tickets activés.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(TicketCog())