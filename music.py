"""
This example cog demonstrates basic usage of Lavalink.py, using the DefaultPlayer.
As this example primarily showcases usage in conjunction with discord.py, you will need to make
modifications as necessary for use with another Discord library.

Usage of this cog requires Python 3.6 or higher due to the use of f-strings.
Compatibility with Python 3.5 should be possible if f-strings are removed.
"""
import re
import math
from pprint import pprint

import discord
import lavalink
from discord.ext import commands
from lyricsgenius import Genius
from discord import Spotify
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

url_rx = re.compile(r'https?://(?:www\.)?.+')


class LavalinkVoiceClient(discord.VoiceClient):
    """
    This is the preferred way to handle external voice sending
    This client will be created via a cls in the connect method of the channel
    see the following documentation:
    https://discordpy.readthedocs.io/en/latest/api.html#voiceprotocol
    """

    def __init__(self, client: commands.bot, channel: discord.abc.Connectable):
        self.client = client
        self.channel = channel
        # ensure there exists a client already
        if hasattr(self.client, 'lavalink'):
            self.lavalink = self.client.lavalink
        else:
            self.client.lavalink = lavalink.Client(client.user.id)
            self.client.lavalink.add_node()
            self.lavalink = self.client.lavalink

    async def on_voice_server_update(self, data):
        # the data needs to be transformed before being handed down to
        # voice_update_handler
        lavalink_data = {
            't': 'VOICE_SERVER_UPDATE',
            'd': data
        }
        await self.lavalink.voice_update_handler(lavalink_data)

    async def on_voice_state_update(self, data):
        # the data needs to be transformed before being handed down to
        # voice_update_handler
        lavalink_data = {
            't': 'VOICE_STATE_UPDATE',
            'd': data
        }
        await self.lavalink.voice_update_handler(lavalink_data)

    async def connect(self, *, timeout: float, reconnect: bool, self_deaf: bool = False,
                      self_mute: bool = False) -> None:
        """
        Connect the bot to the voice channel and create a player_manager
        if it doesn't exist yet.
        """
        # ensure there is a player_manager when creating a new voice_client
        self.lavalink.player_manager.create(guild_id=self.channel.guild.id)
        await self.channel.guild.change_voice_state(channel=self.channel, self_mute=self_mute, self_deaf=self_deaf)

    async def disconnect(self, *, force: bool = False) -> None:
        """
        Handles the disconnect.
        Cleans up running player and leaves the voice client.
        """
        player = self.lavalink.player_manager.get(self.channel.guild.id)

        # no need to disconnect if we are not connected
        if not force and not player.is_connected:
            return

        # None means disconnect
        await self.channel.guild.change_voice_state(channel=None)

        # update the channel_id of the player to None
        # this must be done because the on_voice_state_update that
        # would set channel_id to None doesn't get dispatched after the
        # disconnect
        player.channel_id = None
        self.cleanup()


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        if not hasattr(bot, 'lavalink'):  # This ensures the client isn't overwritten during cog reloads.
            bot.lavalink = lavalink.Client(bot.user.id)
            bot.lavalink.add_node()  # Host, Port, Password, Region, Name

        lavalink.add_event_hook(self.track_hook)

    def cog_unload(self):
        """ Cog unload handler. This removes any event hooks that were registered. """
        self.bot.lavalink._event_hooks.clear()

    async def cog_before_invoke(self, ctx):
        """ Command before-invoke handler. """
        guild_check = ctx.guild is not None
        #  This is essentially the same as `@commands.guild_only()`
        #  except it saves us repeating ourselves (and also a few lines).

        if guild_check:
            await self.ensure_voice(ctx)
            #  Ensure that the bot and command author share a mutual voicechannel.

        return guild_check

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            await ctx.send(error.original)
            # The above handles errors thrown in this cog and shows them to the user.
            # This shouldn't be a problem as the only errors thrown in this cog are from `ensure_voice`
            # which contain a reason string, such as "Join a voicechannel" etc. You can modify the above
            # if you want to do things differently.

    async def ensure_voice(self, ctx):
        """ This check ensures that the bot and command author are in the same voicechannel. """
        player = self.bot.lavalink.player_manager.create(ctx.guild.id, endpoint=str(ctx.guild.region))
        # Create returns a player if one exists, otherwise creates.
        # This line is important because it ensures that a player always exists for a guild.

        # Most people might consider this a waste of resources for guilds that aren't playing, but this is
        # the easiest and simplest way of ensuring players are created.

        # These are commands that require the bot to join a voicechannel (i.e. initiating playback).
        # Commands such as volume/skip etc don't require the bot to be in a voicechannel so don't need listing here.
        should_connect = ctx.command.name in ('play', 'search_music', 'playuser', 'join')

        if not ctx.author.voice or not ctx.author.voice.channel:
            # Our cog_command_error handler catches this and sends it to the voicechannel.
            # Exceptions allow us to "short-circuit" command invocation via checks so the
            # execution state of the command goes no further.
            raise commands.CommandInvokeError('Join a voicechannel first.')

        if not player.is_connected:
            if not should_connect:
                raise commands.CommandInvokeError('Not connected.')

            permissions = ctx.author.voice.channel.permissions_for(ctx.me)

            if not permissions.connect or not permissions.speak:  # Check user limit too?
                raise commands.CommandInvokeError('I need the `CONNECT` and `SPEAK` permissions.')

            player.store('channel', ctx.channel.id)
            await ctx.author.voice.channel.connect(cls=LavalinkVoiceClient)
        else:
            if int(player.channel_id) != ctx.author.voice.channel.id:
                raise commands.CommandInvokeError('You need to be in my voicechannel.')

    async def track_hook(self, event):
        if isinstance(event, lavalink.events.QueueEndEvent):
            # When this track_hook receives a "QueueEndEvent" from lavalink.py
            # it indicates that there are no tracks left in the player's queue.
            # To save on resources, we can tell the bot to disconnect from the voicechannel.
            guild_id = int(event.player.guild_id)
            guild = self.bot.get_guild(guild_id)
            await guild.voice_client.disconnect(force=True)

    @commands.command()
    async def join(self, ctx):
        return

    @commands.command(aliases=['p'])
    async def play(self, ctx, *, query):
        """ Searches and plays a song from a given query. """
        # Get the player for this guild from cache.
        client_credentials_manager = SpotifyClientCredentials(client_id=,
                                                              client_secret=)
        sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        # Remove leading and trailing <>. <> may be used to suppress embedding links in Discord.
        query = query.strip('<>')
        global spotify
        spotify = False

        try:
            print("trying to find the song with the spotify track class")
            urn = query
            print(urn)
            track = sp.track(urn)
            query = f'ytsearch:{track["name"]} {track["artists"][0]["name"]}'
            print(query)
        except:
            try:
                pl_id = f'{query}'
                offset = 0
                response = sp.playlist_items(pl_id,
                                             offset=offset,
                                             fields='items.track.id,total',
                                             additional_types=['track'])
                playlist_size = 50
                while playlist_size != 0:
                    playlist_size = playlist_size - 1
                    songs = response['items'][playlist_size - 1]['track']['id']
                    track = sp.track(songs)
                    pprint(f"{track['name']} {track['artists'][0]['name']}")
                    query = f"ytsearch:{track['name']} {track['artists'][0]['name']}"

                #playlist_link = f"{query}"
                #playlist_URI = playlist_link.split("/")[-1].split("?")[0]
                #print("trying to find the song with the spotify playlist class")
                #for track in sp.playlist_tracks(playlist_URI)["items"]:
                #    track_name = track["track"]["name"]
                #    artist_name = track["track"]["artists"][0]["name"]
                #    query = f'ytsearch:{track_name} {artist_name}'
                #    player.queue.put(query)

            except:
        # Check if the user input might be a URL. If it isn't, we can Lavalink do a YouTube search for it instead.
        # SoundCloud searching is possible by prefixing "scsearch:" instead.
                if not url_rx.match(query):
                    try:
                        query = f'ytsearch:{query}'
                    except:
                        query = f'scsearch:{query}'

        # Get the results for the query from Lavalink.
        results = await player.node.get_tracks(query)

        # Results could be None if Lavalink returns an invalid response (non-JSON/non-200 (OK)).
        # Alternatively, results['tracks'] could be an empty array if the query yielded no tracks.
        if not results or not results['tracks']:
            return await ctx.send('Nothing found!')

        embed = discord.Embed(color=discord.Color.blurple())

        # Valid loadTypes are:
        #   TRACK_LOADED    - single video/direct URL)
        #   PLAYLIST_LOADED - direct URL to playlist)
        #   SEARCH_RESULT   - query prefixed with either ytsearch: or scsearch:.
        #   NO_MATCHES      - query yielded no results
        #   LOAD_FAILED     - most likely, the video encountered an exception during loading.
        if results['loadType'] == 'PLAYLIST_LOADED':
            tracks = results['tracks']

            for track in tracks:
                # Add all of the tracks from the playlist to the queue.
                player.add(requester=ctx.author.id, track=track)

            embed.title = 'Playlist Enqueued!'
            embed.description = f'{results["playlistInfo"]["name"]} - {len(tracks)} tracks'
        else:
            track = results['tracks'][0]
            embed.title = 'Track Enqueued'
            embed.description = f'[{track["info"]["title"]}]({track["info"]["uri"]})'

            # You can attach additional information to audiotracks through kwargs, however this involves
            # constructing the AudioTrack class yourself.
            track = lavalink.models.AudioTrack(track, ctx.author.id, recommended=True)
            player.add(requester=ctx.author.id, track=track)

        await ctx.send(embed=embed)
        global lyricstitle
        lyricstitle = query.strip('ytsearch:')
        # We don't want to call .play() if the player is playing as that will effectively skip
        # the current track.
        if not player.is_playing:
            await player.play()

    @commands.command(aliases=['pu'])
    async def playuser(self, ctx, member: discord.Member):
        """ Searches and plays a song from a given query. """
        # Get the player for this guild from cache.
        global spotify
        spotify = True
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if member is None:
            member = ctx.author
        if member.activities:
            for activity in member.activities:
                if isinstance(activity, Spotify):
                    query = f'{activity.title} {activity.artist}'
                    global spotifylyrics
                    spotifylyrics = f'{activity.title} {activity.artist}'
                    # Check if the user input might be a URL. If it isn't, we can Lavalink do a YouTube search for it instead.
                    # SoundCloud searching is possible by prefixing "scsearch:" instead.

                    if not url_rx.match(query):
                        try:
                            query = f'ytsearch:{query}'
                        except:
                            query = f'scsearch:{query}'
                        spotifylyrics = query
                    # Get the results for the query from Lavalink.
                    results = await player.node.get_tracks(query)

                    # Results could be None if Lavalink returns an invalid response (non-JSON/non-200 (OK)).
                    # ALternatively, resullts['tracks'] could be an empty array if the query yielded no tracks.
                    if not results or not results['tracks']:
                        return await ctx.send('Nothing found!')

                    embed = discord.Embed(
                        color=discord.Color.blurple()
                    )

                    # Valid loadTypes are:
                    #   TRACK_LOADED    - single video/direct URL)
                    #   PLAYLIST_LOADED - direct URL to playlist)
                    #   SEARCH_RESULT   - query prefixed with either ytsearch: or scsearch:.
                    #   NO_MATCHES      - query yielded no results
                    #   LOAD_FAILED     - most likely, the video encountered an exception during loading.
                    if results['loadType'] == 'PLAYLIST_LOADED':
                        tracks = results['tracks']

                        for track in tracks:
                            # Add all of the tracks from the playlist to the queue.
                            player.add(requester=ctx.author.id, track=track)

                        embed.title = 'Playlist Enqueued!'
                        embed.description = f'{results["playlistInfo"]["name"]} - {len(tracks)} tracks'
                    else:
                        track = results['tracks'][0]
                        embed.title = 'Track Enqueued'
                        embed.description = f'[{track["info"]["title"]}]({track["info"]["uri"]})'

                        # You can attach additional information to audiotracks through kwargs, however this involves
                        # constructing the AudioTrack class yourself.
                        track = lavalink.models.AudioTrack(track, ctx.author.id, recommended=True)
                        player.add(requester=ctx.author.id, track=track)

                    await ctx.send(embed=embed)

                    # We don't want to call .play() if the player is playing as that will effectively skip
                    # the current track.
                    if not player.is_playing:
                        await player.play()
                else:
                    await ctx.send("Make sure this user has connected discord with spotify and displays their song as a status.")

    @commands.command(name="Search music", help="Looks for 10 tracks with the given name", aliases=["look", "find"])
    async def search_music(self, ctx, *, search):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        query = f'ytsearch:{search}'
        results = await player.node.get_tracks(query)
        tracks = results['tracks'][0:10]
        i = 0

        query_result = ''
        for track in tracks:
            i = i + 1
            query_result = query_result + f'{i}) {track["info"]["title"]} - {track["info"]["uri"]}\n'

        embed = discord.Embed(
            title=f"Search results for {search}",
            description=query_result,
            color=ctx.author.color
        )

        await ctx.channel.send(embed=embed)

        def check(m):
            return m.author.id == ctx.author.id

        response = await self.bot.wait_for('message', check=check)
        track = tracks[int(response.content) - 1]

        player.add(requester=ctx.author.id, track=track)

        if not player.is_playing:
            await player.play()

    @commands.command(name='pause')
    async def pause(self, ctx):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        await player.set_pause(True)
        await ctx.message.add_reaction("⏸")

    @commands.command(name='resume')
    async def resume(self, ctx):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        await player.set_pause(False)
        await ctx.message.add_reaction("⏯")

    @commands.command(name='volume', help="Set your volume. The limit is set to 1000")
    async def volume(self, ctx, volume: int = 100):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        await player.set_volume(volume)
        await ctx.send(f'🔈 | Set volume to {player.volume}%')

    @commands.command()
    async def loop(self, ctx):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        player.set_repeat(True)
        await ctx.message.add_reaction("🔁")

    @commands.command()
    async def unloop(self, ctx):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if player.repeat is True:
            player.set_repeat(False)
            await ctx.message.add_reaction("🔁")
        else:
            await ctx.send("This track is not looped")
            await ctx.message.add_reaction("❌")

    @commands.command(name="current", description="Shows the current playing song.",
                      aliases=['np', 'nowplaying', "now", "n"])
    async def current(self, ctx):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if player.current:
            pos = lavalink.format_time(player.position)
            if player.current.stream:
                dur = 'LIVE'
            else:
                dur = lavalink.format_time(player.current.duration)

        embed = discord.Embed(
            title="**Now Playing**",
            description=player.current.title,
            color=discord.Color.blurple()
        )
        embed.add_field(name="Uploader", value=player.current.author)
        embed.add_field(name="Track Url", value=f"[Click]({player.current.uri})")
        embed.add_field(name="Requester", value=f"<@{player.current.requester}>")

        if player.current.stream:
            value = "🔴︱Live"
            time = pos
            embed.add_field(name="🔈 | Volume", value=f"{player.volume}%")
            embed.add_field(name="Duration", value=value)
            embed.add_field(name="Playtime", value=time)

        else:
            value = f"{pos}/{dur}"
            embed.add_field(name="🔈 | Volume", value=f"{player.volume}%")
            embed.add_field(name="\u200b", value="\u200b")
            embed.add_field(name="Duration", value=value)

        await ctx.send(embed=embed)

    @commands.command(help="Skips the current playing song", aliases=["s"], name="skip")
    async def skip(self, ctx):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        await player.skip()
        await ctx.message.add_reaction("⏭")

    @commands.command(aliases=['q'])
    async def queue(self, ctx, page: int = 1):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if not player.queue:
            return await ctx.send('There\'s nothing in the queue! Why not queue something?')

        items_per_page = 10
        pages = math.ceil(len(player.queue) / items_per_page)

        start = (page - 1) * items_per_page
        end = start + items_per_page

        queue_list = ''
        for i, track in enumerate(player.queue[start:end], start=start):
            queuetime = lavalink.format_time(track.duration)

        for i, track in enumerate(player.queue[start:end], start=start):
            queue_list += f'`{i + 1}.` [**{track.title}**]({track.uri})\n'

        embed = discord.Embed(
            colour=ctx.guild.me.top_role.colour,
            description=f'**{len(player.queue)} tracks**\ntotal queue time = {queuetime}\n\n{queue_list}',
            color=ctx.author.color
        )
        embed.set_footer(text=f'Viewing page {page}/{pages}')
        await ctx.send(embed=embed)

    @commands.command()
    async def remove(self, ctx, index: int):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if not player.queue:
            return await ctx.send('Nothing queued.')

        if index > len(player.queue) or index < 1:
            return await ctx.send('Index has to be >=1 and <=queue size')

        index = index - 1
        removed = player.queue.pop(index)

        await ctx.send('Removed **' + removed.title + '** from the queue.')

    @commands.command()
    async def shuffle(self, ctx):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if not player.is_playing:
            return await ctx.send('Nothing playing.')

        player.shuffle = not player.shuffle

        await ctx.send('🔀 | Shuffle ' + ('enabled' if player.shuffle else 'disabled'))

    @commands.command(help="Search for the lyrics of a song.", hidden=True)
    async def lyrics(self, ctx, *, song=None):
        token =
        genius = Genius(token)
        genius.remove_section_headers = True
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        try:
            new = player.current.title
            stopwords = ['(Lyrics)', 'Official', 'Video', '(Official Video)', 'lyrics', ' (Audio)', '(Official Music Video)']
            lyricwords = new.split()

            resultwords = [word for word in lyricwords if word not in stopwords]
            titles = ' '.join(resultwords)
            print(titles)
        except:
            new = player.current.title
            stopwords = ['(lyrics)', 'official', 'video', 'lyrics', '(official  music  video)', '(audio)']
            lyricwords = new.split()

            resultwords = [word for word in lyricwords if word.lower() not in stopwords]
            titles = ' '.join(resultwords)
            print(titles)
        if song is None:
            print(spotify)
            if spotify == True:
                print("searching with spotify song data")
                lyric = genius.search_song(f"{spotifylyrics}")
                print(lyric)
                embed = discord.Embed(
                    title=player.current.title,
                    description=lyric.lyrics,
                    colour=discord.Colour.blurple()
                )
                await ctx.send(embed=embed)
            elif spotify == False:
                try:
                    search = lyricstitle
                    lyric = genius.search_song(f"{search}")
                    embed = discord.Embed(
                        title=player.current.title,
                        description=lyric.lyrics,
                        colour=discord.Colour.blurple()
                    )
                    await ctx.send(embed=embed)
                except:
                    try:
                        search0 = titles.split()[0]
                        search1 = titles.split()[1]
                        search2 = titles.split()[2]
                        search3 = titles.split()[3]
                        search4 = titles.split()[4]
                        search5 = titles.split()[5]
                        search = "{}  {}  {}  {}  {}  {}".format(search0, search1, search2, search3, search4, search5)
                    except:
                        try:
                            search0 = titles.split()[0]
                            search1 = titles.split()[1]
                            search2 = titles.split()[2]
                            search3 = titles.split()[3]
                            search4 = titles.split()[4]
                            search = "{}  {}  {}  {}  {}".format(search0, search1, search2, search3, search4)
                        except:
                            try:
                                search0 = titles.split()[0]
                                search1 = titles.split()[1]
                                search2 = titles.split()[2]
                                search3 = titles.split()[3]
                                search = "{}  {}  {}  {}".format(search0, search1, search2, search3)
                            except:
                                try:
                                    search0 = titles.split()[0]
                                    search1 = titles.split()[1]
                                    search2 = titles.split()[2]
                                    search = "{}  {}  {}".format(search0, search1, search2)
                                except:
                                    try:
                                        search0 = titles.split()[0]
                                        search1 = titles.split()[1]
                                        search = "{}  {}".format(search0, search1)
                                    except:
                                        await ctx.send("Lyrics not Found")
        else:
            song = genius.search_song(f"{song}")
            async with ctx.typing():
                lyrics = discord.Embed(
                    title=f"{song}",
                    description=song.lyrics,
                    colour=discord.Colour.blurple()
                )
                await ctx.send(embed=lyrics)

    @commands.command(help="Search for the lyrics of a song.", aliases=['lu'], hidden=True)
    async def lyricsuser(self, ctx, member: discord.Member):
        token =
        genius = Genius(token)
        genius.remove_section_headers = True
        if member.activities:
            for activity in member.activities:
                if isinstance(activity, Spotify):
                    song = f'{activity.title}'
                    lyricsz = genius.search_song(f"{song}")
                    async with ctx.typing():
                        lyrics = discord.Embed(
                            title=f"{song}",
                            description=lyricsz.lyrics,
                            colour=discord.Colour.blurple()
                        )
                        await ctx.send(embed=lyrics)

    @commands.command(name="disconnect", help="Disconnects the player from the voice channel and clears its queue.",
                      aliases=['dc'])
    async def disconnect(self, ctx):
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if not player.is_connected:
            # We can't disconnect, if we're not connected.
            return await ctx.send('Not connected.')

        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            # Abuse prevention. Users not in voice channels, or not in the same voice channel as the bot
            # may not disconnect the bot.
            return await ctx.send('You\'re not in my voicechannel!')

        # Clear the queue to ensure old tracks don't start playing
        # when someone else queues something.
        player.queue.clear()
        # Stop the current track so Lavalink consumes less resources.
        await player.stop()
        # Disconnect from the voice channel.
        await ctx.voice_client.disconnect(force=True)
        await ctx.send('*⃣ | Disconnected.')


def setup(bot):
    bot.add_cog(Music(bot))
