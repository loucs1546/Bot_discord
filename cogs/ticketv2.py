# cogs/tickets_v2.py
import discord
from discord.ext import commands
from utils.logging import send_log_to
import core_config as config
import datetime
import io

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Créer un ticket", style=discord.ButtonStyle.success, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user

        # Vérifie si l'utilisateur a déjà un ticket ouvert
        for channel in guild.channels:
            if channel.name == f"ticket-{user.id}":
                await interaction.response.send_message(
                    "Vous avez déjà un ticket ouvert !", ephemeral=True
                )
                return

        # Création du salon de ticket
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        }

        ticket_channel = await guild.create_text_channel(
            name=f"ticket-{user.id}",
            overwrites=overwrites,
            reason=f"Ticket créé par {user} ({user.id})"
        )

        # Embed initial dans le ticket
        embed = discord.Embed(
            title="📬 Nouveau ticket",
            description=f"Bonjour {user.mention},\n\nUn membre de l'équipe vous répondra bientôt.\n\nCliquez sur **Prendre en charge** si vous êtes staff.",
            color=0x5865F2,
            timestamp=datetime.datetime.utcnow()
        )
        embed.set_footer(text="Seiko Security • Système de tickets")
        view = TicketControls(user.id)
        await ticket_channel.send(embed=embed, view=view)

        # Logs création
        log_embed = discord.Embed(
            title="🎟️ Ticket créé",
            description=f"**Utilisateur** : {user.mention} (`{user}`)\n**Salon** : {ticket_channel.mention}",
            color=0x00ff00,
            timestamp=datetime.datetime.utcnow()
        )
        log_embed.set_thumbnail(url=user.display_avatar.url)
        await send_log_to(interaction.client, "ticket", log_embed)

        await interaction.response.send_message(f"✅ Votre ticket a été créé : {ticket_channel.mention}", ephemeral=True)


class TicketControls(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=None)
        self.owner_id = owner_id

    @discord.ui.button(label="🔧 Prendre en charge", style=discord.ButtonStyle.primary, custom_id="claim_ticket")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any(role.permissions.administrator or role.permissions.manage_messages for role in interaction.user.roles):
            await interaction.response.send_message("❌ Vous n’avez pas la permission de prendre en charge ce ticket.", ephemeral=True)
            return

        # Désactiver le bouton après claim
        button.disabled = True
        button.style = discord.ButtonStyle.secondary
        button.label = "✅ Pris en charge"

        # Notification dans le salon
        embed = discord.Embed(
            description=f"✅ Ce ticket est maintenant pris en charge par {interaction.user.mention}.",
            color=0x00ff00
        )
        await interaction.channel.send(embed=embed)

        # Logs de prise en charge
        log_embed = discord.Embed(
            title="🔧 Ticket pris en charge",
            description=f"**Staff** : {interaction.user.mention}\n**Ticket** : {interaction.channel.mention}",
            color=0x00aaff,
            timestamp=datetime.datetime.utcnow()
        )
        await send_log_to(interaction.client, "ticket", log_embed)

        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="🔒 Fermer", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id and not any(role.permissions.administrator for role in interaction.user.roles):
            await interaction.response.send_message("❌ Seul le propriétaire du ticket ou un admin peut le fermer.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        
        # === CAPTURE TOUS LES MESSAGES DU TICKET ===
        messages_history = []
        try:
            async for message in interaction.channel.history(limit=None, oldest_first=True):
                msg_data = {
                    "author": str(message.author),
                    "timestamp": message.created_at.strftime("%d/%m/%Y %H:%M:%S"),
                    "content": message.content or "(pas de texte)",
                    "attachments": [att.url for att in message.attachments] if message.attachments else [],
                }
                messages_history.append(msg_data)
        except Exception as e:
            print(f"❌ Erreur lors de la capture des messages : {e}")

        # === CRÉER UN RÉSUMÉ DES LOGS ===
        if messages_history:
            log_content = f"📋 **RÉSUMÉ DU TICKET** - {interaction.channel.name}\n"
            log_content += f"**Ouvert par** : {messages_history[0]['author']}\n"
            log_content += f"**Fermé par** : {interaction.user}\n"
            log_content += f"**Date de fermeture** : {datetime.datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')}\n"
            log_content += f"**Nombre de messages** : {len(messages_history)}\n\n"
            log_content += "="*50 + "\n\n"
            
            # Boucler sur tous les messages
            for msg in messages_history:
                log_content += f"**{msg['author']}** [{msg['timestamp']}]\n"
                log_content += f"{msg['content']}\n"
                if msg['attachments']:
                    log_content += f"📎 *Pièces jointes* : {', '.join(msg['attachments'])}\n"
                log_content += "\n"

            # Si le contenu est trop long, créer un fichier
            if len(log_content) > 2000:
                # Créer un fichier texte
                log_file = io.BytesIO(log_content.encode('utf-8'))
                file = discord.File(log_file, filename=f"ticket_{interaction.channel.name}.txt")
                
                # Envoyer dans le salon des tickets
                ticket_logs_channel_id = config.CONFIG.get("logs", {}).get("ticket")
                if ticket_logs_channel_id:
                    ticket_channel = interaction.client.get_channel(ticket_logs_channel_id)
                    if ticket_channel:
                        embed = discord.Embed(
                            title="📋 Logs complets du ticket",
                            description=f"**Ticket** : {interaction.channel.name}\n**Fermé par** : {interaction.user.mention}\n**Messages** : {len(messages_history)}",
                            color=0x2f3136,
                            timestamp=datetime.datetime.utcnow()
                        )
                        embed.set_footer(text="Seiko Security • Logs de ticket")
                        await ticket_channel.send(embed=embed, file=file)
            else:
                # Envoyer directement en embeds
                ticket_logs_channel_id = config.CONFIG.get("logs", {}).get("ticket")
                if ticket_logs_channel_id:
                    ticket_channel = interaction.client.get_channel(ticket_logs_channel_id)
                    if ticket_channel:
                        # Split en chunks de 2000 caractères pour ne pas dépasser la limite Discord
                        chunks = [log_content[i:i+2000] for i in range(0, len(log_content), 2000)]
                        for i, chunk in enumerate(chunks):
                            if i == 0:
                                embed = discord.Embed(
                                    title="📋 Logs complets du ticket",
                                    description=chunk,
                                    color=0x2f3136,
                                    timestamp=datetime.datetime.utcnow()
                                )
                            else:
                                embed = discord.Embed(
                                    description=chunk,
                                    color=0x2f3136
                                )
                            await ticket_channel.send(embed=embed)

        # Logs de fermeture dans le salon de logs
        log_embed = discord.Embed(
            title="🔒 Ticket fermé",
            description=f"**Fermé par** : {interaction.user.mention}\n**Ticket** : `{interaction.channel.name}`\n**Messages** : {len(messages_history)}",
            color=0xff0000,
            timestamp=datetime.datetime.utcnow()
        )
        await send_log_to(interaction.client, "ticket", log_embed)

        # Supprimer le canal
        await interaction.channel.delete(reason=f"Ticket fermé par {interaction.user} ({interaction.user.id})")


class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="ticket-panel", description="Envoie le panneau de création de ticket")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def ticket_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎟️ Support - Créer un ticket",
            description="Cliquez sur le bouton ci-dessous pour ouvrir un ticket avec l'équipe.\n\n> ⚠️ **Abuse = Sanction**",
            color=0x2f3136,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Seiko Security • Système sécurisé")
        await interaction.channel.send(embed=embed, view=TicketView())
        await interaction.response.send_message("✅ Pannel de tickets envoyé.", ephemeral=True)


async def setup(bot):
    bot.add_view(TicketView())  # Persistance des boutons après redémarrage
    bot.add_view(TicketControls(0))  # On passe un dummy ID, mais les views sont restaurés via custom_id
    await bot.add_cog(TicketsCog(bot))