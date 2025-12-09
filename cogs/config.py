# cogs/config.py
import discord
from discord.ext import commands

class ConfigCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="config", description="Configure le bot")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def config(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="⚙️ Configuration de Seiko",
            description="Utilisez les boutons ci-dessous.",
            color=discord.Color.blurple()
        )
        view = ConfigMainView(self.bot)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

class ConfigMainView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=600)
        self.bot = bot
        self.add_item(SecurityButton())
        self.add_item(LogsButton())

class SecurityButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🛡️ Sécurité", style=discord.ButtonStyle.primary)
    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(title="Sécurité", description="Paramètres de sécurité")
        await interaction.response.edit_message(embed=embed, view=SecurityView())

class LogsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📜 Logs", style=discord.ButtonStyle.secondary)
    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(title="Logs", description="Paramètres des logs")
        await interaction.response.edit_message(embed=embed, view=LogsView())

class SecurityView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(BackButton())

class LogsView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=600)
        self.add_item(BackButton())

class BackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="⬅️ Retour", style=discord.ButtonStyle.danger)
    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(title="⚙️ Configuration", description="Choisissez une section")
        await interaction.response.edit_message(embed=embed, view=ConfigMainView(interaction.client))

async def setup(bot):
    await bot.add_cog(ConfigCog(bot))