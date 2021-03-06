from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.commands import Context
from random import randint
from datetime import datetime
import discord
import motor.motor_asyncio
import time


class Levels:
    """
    A leveling system for Red.
    """
    __author__ = "Liante#0216"

    # Constants for data access
    XP_GOAL_BASE = "xp_goal_base"
    XP_GAIN_FACTOR = "xp_gain_factor"
    XP_MIN = "xp_min"
    XP_MAX = "xp_max"
    COOLDOWN = "cooldown"
    SINGLE_ROLE = "single_role"
    MAKE_ANNOUNCEMENTS = "make_announcements"
    ACTIVE = "active"

    ROLE_ID = "role_id"
    ROLE_NAME = "role_name"
    AUTOROLES = "autoroles"
    DESCRIPTION = "description"
    DEFAULT_DESC = "No description given."
    DEFAULT_ROLE = "No level roles"

    USER_DATA = "user_data"
    USER_ID = "user_id"
    USERNAME = "username"
    EXP = "exp"
    LEVEL = "level"
    GOAL = "goal"
    LAST_TRIGGER = "last_trigger"
    MESSAGE_COUNT = "message_count"
    MESSAGE_WITH_XP = "message_with_xp"

    DOCUMENT_NAME = "document_name"
    GUILD_INFO = "guild_info"
    GUILD_ID = "guild_id"
    GUILD_NAME = "guild_name"
    GUILD_USERS = "guild_users"
    GUILD_ROLES = "guild_roles"

    GUILD_COLL = "guild_coll"
    GUILD_CONF = "guild_conf"
    USER = "user"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, 4712468135468475)
        default_guild = {
            self.XP_GOAL_BASE: 100,
            self.XP_GAIN_FACTOR: 0.01,
            self.XP_MIN: 15,
            self.XP_MAX: 25,
            self.COOLDOWN: 60,
            self.SINGLE_ROLE: True,
            self.MAKE_ANNOUNCEMENTS: False,
            self.ACTIVE: True
        }

        self.config.register_guild(**default_guild, force_registration=True)

        self.client = motor.motor_asyncio.AsyncIOMotorClient()
        self.levels_db = self.client.levels

    async def on_message(self, message: discord.Message):
        # ignore bots, dms, and red commands
        if message.author.bot:
            return

        if not message.guild:
            return

        prefixes = await Config.get_core_conf().prefix()
        for prefix in prefixes:
            if message.content.startswith(prefix):
                return

        user = message.author
        guild = message.guild
        channel = message.channel

        guild_conf = self.config.guild(guild)
        if not await guild_conf.active():
            return
        guild_coll = await self._get_guild_coll(guild)
        guild_users = await self._get_users(guild_coll=guild_coll)
        user_data = await self._get_user_data(guild_conf=guild_conf, guild_coll=guild_coll,
                                              guild_users=guild_users, user=user)

        user_data[self.MESSAGE_COUNT] = user_data[self.MESSAGE_COUNT] + 1
        guild_users[user_data[self.USER_ID]] = user_data

        last_trigger = user_data[self.LAST_TRIGGER]
        curr_time = time.time()
        cooldown = await guild_conf.cooldown()

        if curr_time - last_trigger <= cooldown:
            return

        level_up = await self._process_xp(guild_conf=guild_conf,
                                          guild_users=guild_users,
                                          user_data=user_data,
                                          user=user)
        await guild_coll.update_one({self.DOCUMENT_NAME: self.GUILD_USERS},
                                    {"$set": {self.GUILD_USERS: guild_users}})

        if level_up and await self.config.guild(guild).make_announcements():
            await channel.send("Congratulations {0}, you're now level {1}".format(user.mention, user_data[self.LEVEL]))

    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # this should handle any nickname and username changes
        if before.display_name == after.display_name:
            return

        guild = before.guild
        guild_conf = self.config.guild(guild)
        guild_coll = await self._get_guild_coll(guild)
        guild_users = await self._get_users(guild_coll=guild_coll)
        user_data = await self._get_user_data(guild_conf=guild_conf, guild_coll=guild_coll,
                                              guild_users=guild_users, user=before)

        user_data[self.USERNAME] = after.display_name
        guild_users[user_data[self.USER_ID]] = user_data
        guild_coll.update_one({self.DOCUMENT_NAME: self.GUILD_USERS},
                              {"$set": {self.GUILD_USERS: guild_users}})

    async def _get_guild_coll(self, guild: discord.Guild):
        """
        Each guild gets a collection and a first document containing the guild's data
        """
        guild_coll = self.levels_db[str(guild.id)]
        if await guild_coll.find_one({self.GUILD_ID: str(guild.id)}) is None:
            guild_info = {
                self.DOCUMENT_NAME: self.GUILD_INFO,
                self.GUILD_ID: str(guild.id),
                self.GUILD_NAME: guild.name
            }
            guild_users = {
                self.DOCUMENT_NAME: self.GUILD_USERS,
                self.GUILD_USERS: {}
            }
            guild_roles = {
                self.DOCUMENT_NAME: self.GUILD_ROLES,
                self.GUILD_ROLES: []
            }
            await guild_coll.insert_one(guild_info)
            await guild_coll.insert_one(guild_users)
            await guild_coll.insert_one(guild_roles)
        return guild_coll

    async def _get_user_data(self, **kwargs):
        """
        Each user is represented by a document inside the guild's collection
        """
        guild_conf = kwargs[self.GUILD_CONF]
        guild_coll = kwargs[self.GUILD_COLL]
        guild_users = kwargs[self.GUILD_USERS]
        user = kwargs[self.USER]

        if user.bot:
            return None
        try:
            user_data = guild_users[str(user.id)]
        except KeyError:
            user_data = {
                self.USER_ID: str(user.id),
                self.USERNAME: user.display_name,
                self.ROLE_NAME: self.DEFAULT_ROLE,
                self.EXP: 0,
                self.LEVEL: 0,
                self.GOAL: await guild_conf.xp_goal_base(),
                self.LAST_TRIGGER: time.time(),
                self.MESSAGE_COUNT: 0,
                self.MESSAGE_WITH_XP: 0
            }
            guild_users[user_data[self.USER_ID]] = user_data
            await guild_coll.update_one({self.DOCUMENT_NAME: self.GUILD_USERS},
                                        {"$set": {self.GUILD_USERS: guild_users}})
        return user_data

    async def _get_roles(self, **kwargs):
        """
        This gets all the configured auto-roles for a given guild.
        """
        guild_coll = kwargs[self.GUILD_COLL]
        cursor = await guild_coll.find_one({self.DOCUMENT_NAME: self.GUILD_ROLES})
        guild_roles = cursor[self.GUILD_ROLES]
        return guild_roles

    async def _get_users(self, **kwargs):
        """
        This gets all the users that have been active and therefore added to the database.
        """
        guild_coll = kwargs[self.GUILD_COLL]
        cursor = await guild_coll.find_one({self.DOCUMENT_NAME: self.GUILD_USERS})
        guild_users = cursor[self.GUILD_USERS]
        return guild_users

    async def _process_xp(self, **kwargs):
        """
        _xp-logic-label:
        XP logic explanation
        ====================

        Based on `Link Mathematics of XP <http://onlyagame.typepad.com/only_a_game/2006/08/mathematics_of_.html>`_
        and `Link Mee6 documentation <http://mee6.github.io/Mee6-documentation/levelxp/>`_ I picked Mee6' polynomial
        formula and added a small xp factor depending on the user's level to make up for the difference between
        the basic progression ratio and the total progression ratio.

        This would normally be achieved by increasing xp rewards based on the task's difficulty. Since the task
        is the same all the time in Discord, e.i. sending messages, this is the workaround I picked. It basically
        translates into similar difficulty at low levels but reachable high levels.
        """
        guild_conf = kwargs[self.GUILD_CONF]
        user_data = kwargs[self.USER_DATA]
        guild_users = kwargs[self.GUILD_USERS]
        user = kwargs[self.USER]

        xp_min = await guild_conf.xp_min()
        xp_max = await guild_conf.xp_max()
        xp_gain_factor = await guild_conf.xp_gain_factor()
        xp_gain = randint(xp_min, xp_max)
        message_xp = xp_gain + int(xp_gain * xp_gain_factor * kwargs[self.USER_DATA][self.LEVEL])

        user_data[self.EXP] = user_data[self.EXP] + message_xp
        user_data[self.LAST_TRIGGER] = time.time()
        user_data[self.MESSAGE_WITH_XP] = user_data[self.MESSAGE_WITH_XP] + 1
        guild_users[user_data[self.USER_ID]] = user_data

        if user_data[self.EXP] >= user_data[self.GOAL]:
            await self._level_up(guild_users=guild_users,
                                 user_data=user_data,
                                 user=user)
            return True
        return False

    async def _level_up(self, **kwargs):
        # Separated for admin commands implementation
        guild_users = kwargs[self.GUILD_USERS]
        user_data = kwargs[self.USER_DATA]
        user = kwargs[self.USER]

        await self._level_xp(guild_users=guild_users, user_data=user_data)
        await self._level_update(guild_users=guild_users,
                                 user_data=user_data,
                                 user=user)
        await self._level_goal(guild_users=guild_users, user_data=user_data)

    async def _level_xp(self, **kwargs):
        guild_users = kwargs[self.GUILD_USERS]
        user_data = kwargs[self.USER_DATA]

        user_data[self.EXP] = user_data[self.EXP] - user_data[self.GOAL]
        guild_users[user_data[self.USER_ID]] = user_data

    async def _level_update(self, **kwargs):
        guild_users = kwargs[self.GUILD_USERS]
        user_data = kwargs[self.USER_DATA]
        user = kwargs[self.USER]

        user_data[self.LEVEL] = user_data[self.LEVEL] + 1
        await self._level_role(guild_users=guild_users, user_data=user_data, user=user)

    async def _level_role(self, **kwargs):
        """
        Checks if the user gets a role by leveling up
        """
        guild_users = kwargs[self.GUILD_USERS]
        user_data = kwargs[self.USER_DATA]
        user: discord.Member = kwargs[self.USER]
        guild_coll = await self._get_guild_coll(user.guild)
        guild_roles = await self._get_roles(guild_coll=guild_coll)
        autoroles = []

        if len(guild_roles) == 0:
            return

        for role in guild_roles:
            autoroles.append(discord.utils.find(lambda r: str(r.id) == role[self.ROLE_ID], user.guild.roles))

        async def _assign_role(index):
            new_role = autoroles[index]
            for _user_role in user.roles:
                if _user_role in autoroles:
                    await user.remove_roles(_user_role, reason="level up")
            await user.add_roles(new_role, reason="level up")
            user_data[self.ROLE_NAME] = guild_roles[index][self.ROLE_NAME]

        if user_data[self.LEVEL] < guild_roles[0][self.LEVEL] and user_data[self.ROLE_NAME] != self.DEFAULT_ROLE:
            for user_role in user.roles:
                if user_role in autoroles:
                    await user.remove_roles(user_role, reason="levels lost")
            user_data[self.ROLE_NAME] = self.DEFAULT_ROLE

        i = 0
        while i < len(guild_roles) - 1:
            if guild_roles[i][self.LEVEL] <= user_data[self.LEVEL] < guild_roles[i + 1][self.LEVEL]:
                if user_data[self.ROLE_NAME] != guild_roles[i][self.ROLE_NAME]:
                    await _assign_role(i)
                break
            i += 1

        i = -1
        if guild_roles[i][self.LEVEL] <= user_data[self.LEVEL]:
            if user_data[self.ROLE_NAME] != guild_roles[i][self.ROLE_NAME]:
                await _assign_role(i)

        guild_users[user_data[self.USER_ID]] = user_data

    async def _level_goal(self, **kwargs):
        # 5 * lvl**2 + 50 * lvl + 100 see :this:`xp-logic-label` for more info.
        guild_users = kwargs[self.GUILD_USERS]
        user_data = kwargs[self.USER_DATA]

        user_data[self.GOAL] = 5 * user_data[self.LEVEL] ** 2 + 50 * user_data[self.LEVEL] + 100
        guild_users[user_data[self.USER_ID]] = user_data

    async def _give_xp(self, **kwargs):
        guild_users = kwargs[self.GUILD_USERS]
        user_data = kwargs[self.USER_DATA]
        user = kwargs[self.USER]
        exp = kwargs[self.EXP]

        user_data[self.EXP] += exp
        guild_users[user_data[self.USER_ID]] = user_data

        count = 0
        while user_data[self.EXP] >= user_data[self.GOAL]:
            await self._level_up(guild_users=guild_users,
                                 user_data=user_data,
                                 user=user)
            count += 1
        return count

    @commands.guild_only()
    @commands.command(name="level", aliases=["lvl"])
    async def level_check(self, ctx: Context, user: discord.Member = None):
        """
        Displays your current level.

        Mention someone to know theirs.
        """
        if user is None:
            user = ctx.author
        if user.bot:
            await ctx.send("Bots can't play levels =(")
            return

        guild = ctx.guild
        guild_conf = self.config.guild(guild)
        guild_coll = await self._get_guild_coll(guild)
        guild_users = await self._get_users(guild_coll=guild_coll)
        user_data = await self._get_user_data(guild_conf=guild_conf, guild_coll=guild_coll,
                                              guild_users=guild_users, user=user)
        if user_data is None:
            await ctx.send("User not registered in the database")
            return

        embed = await self._level_embed(ctx, user, user_data)
        await ctx.send(embed=embed)

    async def _level_embed(self, ctx: Context, user: discord.Member, user_data):
        """
        Internal method to format the level card embed
        """
        current_lvl = user_data[self.LEVEL]
        current_exp = user_data[self.EXP]
        next_goal = user_data[self.GOAL]
        level_role = user_data[self.ROLE_NAME]
        username = user_data[self.USERNAME]
        color = user.color

        embed = discord.Embed(title=username, description=user.top_role.name, color=color)
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon_url)
        embed.set_thumbnail(url=user.avatar_url)
        embed.add_field(name="Level", value=current_lvl, inline=True)
        embed.add_field(name="Role", value=level_role, inline=True)
        embed.add_field(name="XP", value=current_exp, inline=True)
        embed.add_field(name="Goal", value=next_goal, inline=True)
        embed.timestamp = datetime.utcnow()

        return embed

    @commands.command(name="levelboard", aliases=["lb", "lvlboard"])
    async def leaderboard(self, ctx: Context):
        """
        Display a leaderboard of the top 20 members in the guild
        """
        guild_coll = await self._get_guild_coll(ctx.guild)
        guild_users = await self._get_users(guild_coll=guild_coll)
        all_users = guild_users.values()
        if len(all_users) == 0:
            await ctx.send("No user activity registered.")
            return

        all_users = sorted(all_users, key=lambda u: (u[self.LEVEL], u[self.EXP]), reverse=True)
        top_user = discord.utils.find(lambda m: m.display_name == all_users[0][self.USERNAME], ctx.guild.members)
        user_list = ""

        embed = discord.Embed(title="------------------------------**Leaderboard**------------------------------")
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon_url)
        embed.set_thumbnail(url=top_user.avatar_url)
        embed.timestamp = datetime.utcnow()

        i = 0
        while i < 20 and i < len(all_users):
            if i == 0:
                suffix = "st"
            elif i == 1:
                suffix = "nd"
            elif i == 2:
                suffix = "rd"
            else:
                suffix = "th"

            user = all_users[i]
            user_list += "{0}{1}. <@!{2}>\t**lvl**: {3}\n".format(i + 1, suffix, user[self.USER_ID], user[self.LEVEL])
            i += 1

        embed.description = user_list
        await ctx.send(embed=embed)

    @checks.admin()
    @commands.guild_only()
    @commands.group(aliases=["la"], autohelp=True)
    async def lvladmin(self, ctx: Context):
        """Admin commands."""
        pass

    @lvladmin.group(autohelp=True)
    async def guild(self, ctx: Context):
        """Guild options"""
        pass

    @guild.group(autohelp=True)
    async def roles(self, ctx: Context):
        """Autoroles options"""
        pass

    @roles.command(name="list")
    async def roles_list(self, ctx: Context):
        """Shows all configured roles"""
        guild_coll = await self._get_guild_coll(ctx.guild)
        guild_roles = await self._get_roles(guild_coll=guild_coll)
        embed = discord.Embed(title="Configured Roles:")
        for role in guild_roles:
            embed.add_field(name="Level {0} - {1}".format(role[self.LEVEL], role[self.ROLE_NAME]),
                            value="{}".format(role[self.DESCRIPTION]),
                            inline=False)

        if not embed.fields:
            embed.description = "No autoroles have been defined in this Guild yet."

        embed.set_footer(text="use !la guild roles add <role> <level> [description] to add more")

        await ctx.send(embed=embed)

    @roles.command(name="add")
    async def roles_add(self, ctx: Context, new_role: discord.Role, level: int, *, description=None):
        """
        Adds a new automatic role

        The roles are by default non-cumulative. Cumulative roles are not yet implemented

        Use quotation marks and case sensitive role name in case it can't be mentioned
        """
        role_id = str(new_role.id)
        role_name = new_role.name
        guild_coll = await self._get_guild_coll(ctx.guild)
        guild_roles = await self._get_roles(guild_coll=guild_coll)

        for role in guild_roles:
            if role[self.ROLE_ID] == role_id or role[self.LEVEL] == level:
                await ctx.send("**{0}** has already been assigned to level {1}!".format(role[self.ROLE_NAME],
                                                                                        role[self.LEVEL]))
                return

        if description is None:
            description = self.DEFAULT_DESC
        role_config = {
            self.ROLE_ID: role_id,
            self.ROLE_NAME: role_name,
            self.LEVEL: level,
            self.DESCRIPTION: description
        }

        guild_roles.append(role_config)
        guild_roles.sort(key=lambda k: k[self.LEVEL])
        await guild_coll.update_one({self.DOCUMENT_NAME: self.GUILD_ROLES},
                                    {"$set": {self.GUILD_ROLES: guild_roles}})
        await ctx.send("{0} will be automatically earned at level {1}".format(new_role.name, level))

    @roles.command(name="remove", aliases=["rm"])
    async def roles_remove(self, ctx: Context, old_role: discord.Role):
        """
        Removes a previously set up automatic role

        Use quotation marks and case sensitive role name in case it can't be mentioned
        """
        role_id = str(old_role.id)
        guild_coll = await self._get_guild_coll(ctx.guild)
        guild_roles = await self._get_roles(guild_coll=guild_coll)

        for role in guild_roles:
            if role_id == role[self.ROLE_ID]:
                guild_roles.remove(role)
                guild_coll.update_one({self.DOCUMENT_NAME: self.GUILD_ROLES},
                                      {"$set": {self.GUILD_ROLES: guild_roles}})
                await ctx.send("The role {} has been removed".format(role[self.ROLE_NAME]))
                return

        await ctx.send("Role not found in database")

    @guild.command(name="reset")
    async def guild_reset(self, ctx: Context):
        """
        Deletes ***all*** stored data of the guild.

        This doesn't ask for confirmation and deletes the whole player database
        """
        guild_coll = await self._get_guild_coll(ctx.guild)
        await guild_coll.drop()
        await ctx.send("The guild's data has been wiped.")

    @guild.command(name="levelboard", aliases=["lb", "lvlboard"])
    async def admin_leaderboard(self, ctx: Context):
        """
        Display a leaderboard with the top 20 members of the guild

        this one contains a xpmsgs / msgs column for statistics. Msgs is the total amount of messages sent by a user and
        xpmsgs is the amount of those messages sent off cooldown and awarded xp. It helps when tuning the cooldown and
        xp settings.
        """
        guild_coll = await self._get_guild_coll(ctx.guild)
        guild_users = await self._get_users(guild_coll=guild_coll)
        all_users = guild_users.values()
        if len(all_users) == 0:
            await ctx.send("No user activity registered.")
            return

        all_users = sorted(all_users, key=lambda u: (u[self.LEVEL], u[self.EXP]), reverse=True)
        top_user = discord.utils.find(lambda m: m.display_name == all_users[0][self.USERNAME], ctx.guild.members)
        user_list = ""

        embed = discord.Embed(title="------------------------------**Leaderboard**------------------------------")
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon_url)
        embed.set_thumbnail(url=top_user.avatar_url)
        embed.timestamp = datetime.utcnow()

        i = 0
        while i < 20 and i < len(all_users):
            if i == 0:
                suffix = "st"
            elif i == 1:
                suffix = "nd"
            elif i == 2:
                suffix = "rd"
            else:
                suffix = "th"

            user = all_users[i]
            user_list += "{0}{1}. <@!{2}>\t**lvl**: {3}\t**msgs**: {4}/{5}\n".format(i + 1,
                                                                                     suffix,
                                                                                     user[self.USER_ID],
                                                                                     user[self.LEVEL],
                                                                                     user[self.MESSAGE_WITH_XP],
                                                                                     user[self.MESSAGE_COUNT])
            i += 1

        embed.description = user_list
        await ctx.send(embed=embed)

    @lvladmin.group(autohelp=True)
    async def user(self, ctx: Context):
        """User options"""
        pass

    @user.command(name="reset")
    async def user_reset(self, ctx: Context, user: discord.Member):
        """
        Deletes ***all*** stored data of a user.

        At the moment this command does not ask for confirmation, so use it carefully.

        user: Mention the user whose data you want to delete.
        """
        guild_coll = await self._get_guild_coll(ctx.guild)
        guild_users = await self._get_users(guild_coll=guild_coll)
        if str(user.id) in guild_users:
            del guild_users[str(user.id)]
            await guild_coll.update_one({self.DOCUMENT_NAME: self.GUILD_USERS},
                                        {"$set": {self.GUILD_USERS: guild_users}})
            await ctx.send("Data for {} has been deleted!".format(user.mention))
            return
        await ctx.send("No data for {} has been found".format(user.mention))

    @user.command(name="setlevel", aliases=["lvl", "level"])
    async def set_level(self, ctx: Context, user: discord.Member, level: int):
        """
        Changes the level of a user.

        user: Mention the user to which you want to change the level.

        level: The new user level.
        """

        guild_conf = self.config.guild(ctx.guild)
        guild_coll = await self._get_guild_coll(ctx.guild)
        guild_users = await self._get_users(guild_coll=guild_coll)
        user_data = await self._get_user_data(guild_conf=guild_conf, guild_coll=guild_coll,
                                              guild_users=guild_users, user=user)
        if user_data is None:
            await ctx.send("No data found for {}".format(user.mention))
            return

        user_data[self.LEVEL] = level
        await self._level_role(guild_users=guild_users, user_data=user_data, user=user)
        await self._level_goal(guild_users=guild_users, user_data=user_data)
        await guild_coll.update_one({self.DOCUMENT_NAME: self.GUILD_USERS},
                                    {"$set": {self.GUILD_USERS: guild_users}})
        await ctx.send("Level of {0} has been changed to {1}".format(user.mention, level))

    @user.command(name="givexp", aliases=["xp"])
    async def give_xp(self, ctx: Context, user: discord.Member, xp: int, *, reason: str = None):
        """
        Gives xp to a user

        the new level and role will be calculated and assigned automatically

        user: mention the user to whom you want to award xp

        xp: the amount to xp you want to give them

        reason: if there's a particular reason why they deserve it
        """
        guild_conf = self.config.guild(ctx.guild)
        guild_coll = await self._get_guild_coll(ctx.guild)
        guild_users = await self._get_users(guild_coll=guild_coll)
        user_data = await self._get_user_data(guild_conf=guild_conf, guild_coll=guild_coll,
                                              guild_users=guild_users, user=user)

        count = await self._give_xp(guild_users=guild_users, user_data=user_data,
                                    user=user, exp=xp)
        await guild_coll.update_one({self.DOCUMENT_NAME: self.GUILD_USERS},
                                    {"$set": {self.GUILD_USERS: guild_users}})

        if reason is not None:
            reason = " for " + reason
        else:
            reason = ""
        await ctx.send("{0.mention} has received {1} xp{2}!".format(user, xp, reason))

        if count != 0:
            levels = "level" if count == 1 else "levels"
            await ctx.send("{0} {1} were earned by that. New shiny level: {2}".format(count, levels,
                                                                                      user_data[self.LEVEL]))

    @lvladmin.group(name="config", autohelp=True)
    async def configuration(self, ctx: Context):
        """Configuration options"""
        pass

    @configuration.command(name="reset")
    async def config_reset(self, ctx: Context):
        """
        Reset **all** configuration to defaults

        this doesn't ask for confirmation and does not affect the player database
        """
        await self.config.guild(ctx.guild).clear()
        await ctx.send("Configuration defaults have been restored")

    @configuration.group(name="set", autohelp=True)
    async def config_set(self, ctx: Context):
        """Change config values"""
        pass

    @config_set.command(name="goal")
    async def set_xp_goal_base(self, ctx: Context, new_value: int):
        """
        Base goal xp

        This is the xp needed to reach level 1. Subsequent goals are measured with the current level's value.
        """
        await self.config.guild(ctx.guild).xp_goal_base.set(new_value)
        await ctx.send("XP goal base value updated")

    @config_set.command(name="gainfactor", aliases=["gf"])
    async def set_xp_gain_factor(self, ctx: Context, new_value: float):
        """
        Increases the xp reward

        XP gained += XP gained * lvl * this factor
        """
        await self.config.guild(ctx.guild).xp_gain_factor.set(new_value)
        await ctx.send("XP gain factor value updated")

    @config_set.command(name="minxp")
    async def set_xp_min(self, ctx: Context, new_value: int):
        """
        Minimum xp per message

        Note that the real minimum is this * lvl * gain factor
        """
        await self.config.guild(ctx.guild).xp_min.set(new_value)
        await ctx.send("Minimum xp per message value updated")

    @config_set.command(name="maxxp")
    async def set_xp_max(self, ctx: Context, new_value: int):
        """
        Maximum xp per message

        Note that the real maximum is this * lvl * gain factor
        """
        await self.config.guild(ctx.guild).xp_max.set(new_value)
        await ctx.send("Maximum xp per message value updated")

    @config_set.command(name="cooldown")
    async def set_cooldown(self, ctx: Context, new_value: int):
        """
        Time between xp awards

        In seconds
        """
        await self.config.guild(ctx.guild).cooldown.set(new_value)
        await ctx.send("XP cooldown value updated")

    @config_set.command(name="mode")
    async def set_role_mode(self, ctx: Context, new_value: bool):
        """
        Not yet implemented

        Determines if old roles should be removed when a new one is gained by leveling up. Set False to keep them.

        ***this has not yet been implemented***
        """
        await self.config.guild(ctx.guild).single_role.set(new_value)
        await ctx.send("Role mode value updated")

    @config_set.command(name="announce")
    async def set_make_announcements(self, ctx: Context, new_value: bool):
        """
        Public announcements when leveling up

        If true, the bot will announce publicly when someone levels up
        """
        await self.config.guild(ctx.guild).make_announcements.set(new_value)
        value = "enabled" if await self.config.guild(ctx.guild).make_announcements() else "disabled"
        await ctx.send("Public announcements are now {}".format(value))

    @config_set.command(name="active")
    async def set_active(self, ctx: Context, new_value: bool):
        """
        Register xp and monitor messages

        If true, the bot will keep record of messages for xp and leveling purposes. Otherwise it will only listen to
        commands
        """
        await self.config.guild(ctx.guild).active.set(new_value)
        value = "enabled" if await self.config.guild(ctx.guild).active() else "disabled"
        await ctx.send("XP tracking is now {}".format(value))

    @configuration.group(name="get", autohelp=True)
    async def config_get(self, ctx: Context):
        """Check current configuration"""
        pass

    @config_get.command(name="goal")
    async def get_xp_goal_base(self, ctx: Context):
        """
        Base goal xp

        This is the xp needed to reach level 1. Subsequent goals are measured with the current level's value.
        """
        value = await self.config.guild(ctx.guild).xp_goal_base()
        await ctx.send("XP goal base: {}".format(value))

    @config_get.command(name="gainfactor", aliases=["gf"])
    async def get_xp_gain_factor(self, ctx: Context):
        """
        Increases the xp reward

        XP gained += XP gained * lvl * this factor
        """
        value = await self.config.guild(ctx.guild).xp_gain_factor()
        await ctx.send("XP gain factor: {}".format(value))

    @config_get.command(name="minxp")
    async def get_xp_min(self, ctx: Context):
        """
        Minimum xp per message

        Note that the real minimum is this * lvl * gain factor
        """
        value = await self.config.guild(ctx.guild).xp_min()
        await ctx.send("Minimum xp per message: {}".format(value))

    @config_get.command(name="maxxp")
    async def get_xp_max(self, ctx: Context):
        """
        Maximum xp per message

        Note that the real maximum is this * lvl * gain factor
        """
        value = await self.config.guild(ctx.guild).xp_max()
        await ctx.send("Maximum xp per message: {}".format(value))

    @config_get.command(name="cooldown")
    async def get_cooldown(self, ctx: Context):
        """
        Time between xp awards

        In seconds
        """
        value = await self.config.guild(ctx.guild).cooldown()
        await ctx.send("XP cooldown: {}".format(value))

    @config_get.command(name="mode")
    async def get_role_mode(self, ctx: Context):
        """
        Not yet implemented

        Determines if old roles should be removed when a new one is gained by leveling up. Set False to keep them.

        ***this has not yet been implemented***
        """
        value = "single" if await self.config.guild(ctx.guild).single_role() else "multi"
        await ctx.send("The role mode is set to: {}-role".format(value))

    @config_get.command(name="announce")
    async def get_make_announcements(self, ctx: Context):
        """
        Public announcements when leveling up

        If true, the bot will announce publicly when someone levels up
        """
        value = "enabled" if await self.config.guild(ctx.guild).make_announcements() else "disabled"
        await ctx.send("Public announcements are {}".format(value))

    @config_get.command(name="active")
    async def get_active(self, ctx: Context):
        """
        Register xp and monitor messages

        If true, the bot will keep record of messages for xp and leveling purposes. Otherwise it will only listen to
        commands
        """
        value = "enabled" if await self.config.guild(ctx.guild).active() else "disabled"
        await ctx.send("XP tracking is now {}".format(value))
