# utils/views.py
import discord
from discord.ui import View, Button

class ContentReviewView(View):
    def __init__(self, content: str, author: discord.Member, channel: discord.TextChannel, bot):
        super().__init__(timeout=300)
        self.content = content
        self.author = author
        self.channel = channel
        self.bot = bot

    @discord.ui.button(label="✅ Valider", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.channel.send(f"{self.author.mention} (message validé)\n{self.content}")
        await interaction.response.edit_message(content="✅ Message restauré.", view=None)

    @discord.ui.button(label="❌ Refuser", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Message supprimé définitivement.", view=None)