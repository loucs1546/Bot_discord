# cogs/moderation_commands.py
import discord
from discord.ext import commands
from datetime import datetime
import core_config as config
from utils.logging import send_log_to
import re

def get_sanction_channel(bot):
    return bot.get_channel(config.CONFIG["logs"].get("sanctions"))

def est_bavure_raison(raison: str) -> bool:
    if not raison or raison.strip().lower() in ("", "aucune raison"):
        return True
    mots = re.findall(r'\b[a-zA-Z]{2,}\b', raison)
    if len(mots) < 2:
        return True
    voyelles = "aeiouy"
    valid_count = 0
    for mot in mots:
        if any(c.lower() in voyelles for c in mot):
            valid_count += 1
            if valid_count >= 2:
                return False
    return True

class ModerationCommandsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="anti-spam", description="Active/désactive l'anti-spam")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def anti_spam(self, interaction: discord.Interaction, actif: bool):
        config.CONFIG["security"]["anti_spam"] = actif
        await interaction.response.send_message(f"✅ Anti-spam {'activé' if actif else 'désactivé'}.", ephemeral=True)

    @discord.app_commands.command(name="anti-raid", description="Active/désactive l'anti-raid")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def anti_raid(self, interaction: discord.Interaction, actif: bool):
        config.CONFIG["security"]["anti_raid"] = actif
        await interaction.response.send_message(f"✅ Anti-raid {'activé' if actif else 'désactivé'}.", ephemeral=True)

    @discord.app_commands.command(name="anti-hack", description="Active/désactive l'anti-hack")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def anti_hack(self, interaction: discord.Interaction, actif: bool):
        config.CONFIG["security"]["anti_hack"] = actif
        await interaction.response.send_message(f"✅ Anti-hack {'activé' if actif else 'désactivé'}.", ephemeral=True)

    @discord.app_commands.command(name="ping", description="Affiche la latence du bot")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Pong ! Latence : **{latency} ms**", ephemeral=True)

    @discord.app_commands.command(name="clear-salon", description="Supprime tous les messages du salon")
    @discord.app_commands.checks.has_permissions(manage_messages=True)
    async def clear_salon(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=1000)
        await interaction.followup.send(f"🧹 **{len(deleted)}** messages supprimés.", ephemeral=True)

    @discord.app_commands.command(name="delete-salon", description="Supprime un salon")
    @discord.app_commands.describe(salon="Salon à supprimer")
    @discord.app_commands.checks.has_permissions(manage_channels=True)
    async def delete_salon(self, interaction: discord.Interaction, salon: discord.TextChannel):
        await salon.delete(reason=f"Supprimé par {interaction.user}")
        await interaction.response.send_message(f"✅ Salon **{salon.name}** supprimé.", ephemeral=True)

    @discord.app_commands.command(name="delete-categorie", description="Supprime une catégorie et ses salons")
    @discord.app_commands.describe(categorie="Catégorie à supprimer")
    @discord.app_commands.checks.has_permissions(manage_channels=True)
    async def delete_categorie(self, interaction: discord.Interaction, categorie: discord.CategoryChannel):
        await interaction.response.send_message("✅ Suppression en cours...", ephemeral=True)
        for channel in categorie.channels:
            try:
                await channel.delete(reason=f"Supprimé avec la catégorie par {interaction.user}")
            except:
                pass
        try:
            await categorie.delete(reason=f"Supprimé par {interaction.user}")
        except:
            pass

    @discord.app_commands.command(name="say", description="Envoie un message dans un salon")
    @discord.app_commands.describe(salon="Salon cible", contenu="Message à envoyer")
    @discord.app_commands.checks.has_permissions(manage_messages=True)
    async def say(self, interaction: discord.Interaction, salon: discord.TextChannel, contenu: str):
        contenu_nettoye = contenu.replace("\\n", "\n")
        await salon.send(contenu_nettoye)
        await interaction.response.send_message(f"✅ Message envoyé dans {salon.mention}.", ephemeral=True)

    @discord.app_commands.command(name="kick", description="Expulse un membre")
    @discord.app_commands.describe(pseudo="Membre à expulser", raison="Raison du kick")
    @discord.app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, pseudo: discord.Member, raison: str = "Aucune raison"):
        if est_bavure_raison(raison):
            embed = discord.Embed(
                title="⚠️ Bavure détectée",
                description=f"**Modérateur** : {interaction.user.mention}\n**Cible** : {pseudo.mention}\n**Commande** : /kick\n**Raison** : *{raison}*",
                color=0xff6600,
                timestamp=discord.utils.utcnow()
            )
            from utils.views import ContentReviewView
            view = ContentReviewView(raison, pseudo, interaction.channel, self.bot)
            await send_log_to(self.bot, "bavures", embed)
            await send_log_to(self.bot, "bavures", embed, view=view)
            await interaction.response.send_message("❌ La raison est invalide (moins de 2 mots ou texte aléatoire).", ephemeral=True)
            return

        try:
            await pseudo.send(f"⚠️ Vous avez été expulsé de **{interaction.guild.name}** pour : **{raison}**.")
        except:
            pass
        await pseudo.kick(reason=raison)
        embed = discord.Embed(
            title="👢 Kick",
            description=f"**Membre** : {pseudo.mention}\n**Modérateur** : {interaction.user.mention}\n**Raison** : {raison}",
            color=0xff9900,
            timestamp=datetime.utcnow()
        )
        ch = get_sanction_channel(self.bot)
        if ch: await ch.send(embed=embed)
        await interaction.response.send_message(f"✅ {pseudo.mention} expulsé.", ephemeral=True)

    @discord.app_commands.command(name="ban", description="Bannit un membre")
    @discord.app_commands.describe(pseudo="Membre à bannir", temps="Jours de suppression des messages (0 = aucun)", raison="Raison du ban")
    @discord.app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, pseudo: discord.Member, temps: int = 0, raison: str = "Aucune raison"):
        if est_bavure_raison(raison):
            embed = discord.Embed(
                title="⚠️ Bavure détectée",
                description=f"**Modérateur** : {interaction.user.mention}\n**Cible** : {pseudo.mention}\n**Commande** : /ban\n**Raison** : *{raison}*",
                color=0xff6600,
                timestamp=discord.utils.utcnow()
            )
            from utils.views import ContentReviewView
            view = ContentReviewView(raison, pseudo, interaction.channel, self.bot)
            await send_log_to(self.bot, "bavures", embed)
            await send_log_to(self.bot, "bavures", embed, view=view)
            await interaction.response.send_message("❌ La raison est invalide (moins de 2 mots ou texte aléatoire).", ephemeral=True)
            return

        try:
            await pseudo.send(f"⚠️ Vous avez été banni de **{interaction.guild.name}** pour : **{raison}**.")
        except:
            pass
        await pseudo.ban(reason=raison, delete_message_days=temps)
        embed = discord.Embed(
            title="🔨 Ban",
            description=f"**Membre** : {pseudo.mention}\n**Modérateur** : {interaction.user.mention}\n**Raison** : {raison}",
            color=0xff0000,
            timestamp=datetime.utcnow()
        )
        ch = get_sanction_channel(self.bot)
        if ch: await ch.send(embed=embed)
        await interaction.response.send_message(f"✅ {pseudo.mention} banni.", ephemeral=True)

    @discord.app_commands.command(name="warn", description="Avertit un membre")
    @discord.app_commands.describe(pseudo="Membre à avertir", raison="Raison de l'avertissement")
    @discord.app_commands.checks.has_permissions(manage_messages=True)
    async def warn(self, interaction: discord.Interaction, pseudo: discord.Member, raison: str = "Aucune raison"):
        if est_bavure_raison(raison):
            embed = discord.Embed(
                title="⚠️ Bavure détectée",
                description=f"**Modérateur** : {interaction.user.mention}\n**Cible** : {pseudo.mention}\n**Commande** : /warn\n**Raison** : *{raison}*",
                color=0xff6600,
                timestamp=discord.utils.utcnow()
            )
            from utils.views import ContentReviewView
            view = ContentReviewView(raison, pseudo, interaction.channel, self.bot)
            await send_log_to(self.bot, "bavures", embed)
            await send_log_to(self.bot, "bavures", embed, view=view)
            await interaction.response.send_message("❌ La raison est invalide (moins de 2 mots ou texte aléatoire).", ephemeral=True)
            return

        embed = discord.Embed(
            title="⚠️ Avertissement",
            description=f"**Membre** : {pseudo.mention}\n**Modérateur** : {interaction.user.mention}\n**Raison** : {raison}",
            color=0xffff00,
            timestamp=discord.utils.utcnow()
        )
        ch = get_sanction_channel(self.bot)
        if ch: await ch.send(embed=embed)
        await interaction.response.send_message(f"✅ Avertissement envoyé.", ephemeral=True)

    @discord.app_commands.command(name="reachlog", description="Affiche le dernier log d'audit")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def reachlog(self, interaction: discord.Interaction):
        try:
            async for entry in interaction.guild.audit_logs(limit=1):
                log_msg = f"**{entry.action.name}**\n"
                log_msg += f"**Cible** : {getattr(entry, 'target', 'Inconnue')}\n"
                log_msg += f"**Auteur** : {entry.user}\n"
                log_msg += f"**Date** : <t:{int(entry.created_at.timestamp())}:R>"
                await interaction.response.send_message(log_msg, ephemeral=True)
                return
            await interaction.response.send_message("📭 Aucun log trouvé.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur : {e}", ephemeral=True)

    @discord.app_commands.command(name="reach-id", description="Résout un ID Discord (utilisateur, salon, rôle)")
    @discord.app_commands.describe(id="ID à résoudre")
   @check_role_permissions("nom")
    async def reach_id(self, interaction: discord.Interaction, id: str):
        try:
            obj_id = int(id)
        except ValueError:
            await interaction.response.send_message("❌ ID invalide. Doit être un nombre.", ephemeral=True)
            return

        guild = interaction.guild
        results = []

        member = guild.get_member(obj_id)
        if member:
            results.append(f"👤 **Membre** : {member.mention} (`{member}`)")

        channel = guild.get_channel(obj_id)
        if channel:
            results.append(f"💬 **Salon** : {channel.mention} (`{channel.name}`)")

        role = guild.get_role(obj_id)
        if role:
            results.append(f"👑 **Rôle** : {role.mention} (`{role.name}`)")

        if results:
            await interaction.response.send_message(
                f"🔍 Résultats pour l'ID `{id}` :\n" + "\n".join(results),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Aucun utilisateur, salon ou rôle trouvé avec l'ID `{id}`.",
                ephemeral=True
            )
    # Ajoute ces commandes dans la classe ModerationCommandsCog

    @discord.app_commands.command(name="logs-messages", description="Affiche les messages d'un utilisateur")
    @discord.app_commands.describe(user="Utilisateur à filtrer", salon="Salon où envoyer les logs")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def logs_messages(self, interaction: discord.Interaction, user: discord.Member, salon: discord.TextChannel):
        # Cette commande nécessite un stockage des messages (à implémenter)
        await interaction.response.send_message("ℹ️ Commande en développement.", ephemeral=True)

    @discord.app_commands.command(name="logs-moderation", description="Affiche les actions de modération d'un utilisateur")
    @discord.app_commands.describe(user="Utilisateur à filtrer")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def logs_moderation(self, interaction: discord.Interaction, user: discord.Member):
        # Cette commande nécessite un stockage des logs d'audit (à implémenter)
        await interaction.response.send_message("ℹ️ Commande en développement.", ephemeral=True)

    @discord.app_commands.command(name="logs-ticket", description="Affiche les logs de ticket d'un utilisateur")
    @discord.app_commands.describe(user="Utilisateur à filtrer")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def logs_ticket(self, interaction: discord.Interaction, user: discord.Member):
        # À lier avec l'autre bot (via écoute des messages)
        await interaction.response.send_message("ℹ️ Commande en développement.", ephemeral=True)

    @discord.app_commands.command(name="logs-vocal", description="Affiche les activités vocales d'un utilisateur")
    @discord.app_commands.describe(user="Utilisateur à filtrer")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def logs_vocal(self, interaction: discord.Interaction, user: discord.Member):
        # À implémenter via on_voice_state_update
        await interaction.response.send_message("ℹ️ Commande en développement.", ephemeral=True)

    @discord.app_commands.command(name="logs-giveaway", description="Affiche les giveaways créés par un utilisateur")
    @discord.app_commands.describe(user="Utilisateur à filtrer")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def logs_giveaway(self, interaction: discord.Interaction, user: discord.Member):
        # À lier avec l'autre bot (via écoute des messages)
        await interaction.response.send_message("ℹ️ Commande en développement.", ephemeral=True)

    @discord.app_commands.command(name="logs-securite", description="Affiche les alertes de sécurité d'un utilisateur")
    @discord.app_commands.describe(user="Utilisateur à filtrer")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def logs_securite(self, interaction: discord.Interaction, user: discord.Member):
        # À implémenter via les logs de sécurité
        await interaction.response.send_message("ℹ️ Commande en développement.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ModerationCommandsCog(bot))