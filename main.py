import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import yt_dlp
import asyncio
import time

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

# ============================================================
# CYBERPUNK COLOR
# ============================================================
CYBERPUNK_COLOR = discord.Color.from_rgb(90, 20, 160)

# ============================================================
# MUSIC STATE
# ============================================================
class MusicPlayer:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue = []
        self.current = None
        self.audio_url = None
        self.loop = False
        self.is_paused = False
        self.start_time = None
        self.duration = None
        self.message = None
        self.volume = 1.0

    def toggle_loop(self):
        self.loop = not self.loop
        return self.loop

music_players: dict[int, MusicPlayer] = {}

def get_player(guild: discord.Guild) -> MusicPlayer:
    if guild.id not in music_players:
        music_players[guild.id] = MusicPlayer(guild.id)
    return music_players[guild.id]

# ============================================================
# PROGRESS BAR
# ============================================================
def make_progress_bar(current: float, total: float, length: int = 20):
    if total <= 0:
        return "▱" * length
    ratio = current / total
    filled = int(ratio * length)
    empty = length - filled
    return "▰" * filled + "▱" * empty

# ============================================================
# CYBERPUNK MUSIC UI
# ============================================================
class MusicControlView(discord.ui.View):
    def __init__(self, player: MusicPlayer, timeout: float | None = 300):
        super().__init__(timeout=timeout)
        self.player = player

    async def _get_vc(self, interaction: discord.Interaction):
        return interaction.guild.voice_client if interaction.guild else None

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.blurple)
    async def play_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = await self._get_vc(interaction)
        if not vc:
            return await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)
        if vc.is_playing():
            vc.pause()
            self.player.is_paused = True
            await interaction.response.send_message("⏸ Paused.", ephemeral=True)
        else:
            vc.resume()
            self.player.is_paused = False
            await interaction.response.send_message("▶️ Resumed.", ephemeral=True)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.green)
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = await self._get_vc(interaction)
        if not vc:
            return await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)
        if not vc.is_paused():
            return await interaction.response.send_message("Music is not paused.", ephemeral=True)
        vc.resume()
        self.player.is_paused = False
        await interaction.response.send_message("▶️ Resumed.", ephemeral=True)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.primary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = await self._get_vc(interaction)
        if not vc:
            return await interaction.response.send_message("Nothing to skip.", ephemeral=True)
        vc.stop()
        await interaction.response.send_message("⏭ Skipped.", ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.blurple)
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self.player.toggle_loop()
        button.label = "Loop: ON" if state else "Loop: OFF"
        await interaction.response.send_message(f"🔁 Loop {'ON' if state else 'OFF'}", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.red)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = await self._get_vc(interaction)
        if vc:
            vc.pause()
            self.player.is_paused = True
            await interaction.response.send_message("⏹ Stopped (paused, queue kept).", ephemeral=True)

# ============================================================
# NOW PLAYING EMBED
# ============================================================
def make_now_playing_embed(player: MusicPlayer):
    info = player.current
    if not info:
        return discord.Embed(
            title="🎧 S1mplicity — Cyberpunk Player",
            description="Nothing is playing.",
            color=CYBERPUNK_COLOR
        )

    title = info.get("title", "Unknown Title")
    uploader = info.get("uploader", "Unknown Artist")
    thumb = info.get("thumbnail")
    duration = player.duration or 0

    if player.start_time:
        elapsed = time.time() - player.start_time
        elapsed = max(0, min(elapsed, duration))
    else:
        elapsed = 0

    bar = make_progress_bar(elapsed, duration)
    e_m, e_s = divmod(int(elapsed), 60)
    d_m, d_s = divmod(int(duration), 60)

    embed = discord.Embed(
        title="🎧 S1mplicity — Cyberpunk Hybrid Player",
        description=f"**{title}**\n*{uploader}*",
        color=CYBERPUNK_COLOR
    )
    embed.add_field(name="⏱ Progress", value=f"{bar}\n`{e_m}:{e_s:02d} / {d_m}:{d_s:02d}`", inline=False)
    embed.add_field(name="🔁 Loop", value="ON 🔁" if player.loop else "OFF", inline=True)
    embed.add_field(name="🎵 Queue", value=f"{len(player.queue)} tracks", inline=True)
    if thumb:
        embed.set_thumbnail(url=thumb)
    embed.set_footer(text="S1mplicity • Dark Spotify Cyberpunk UI")
    return embed

# ============================================================
# EMBED UPDATER
# ============================================================
@tasks.loop(seconds=2)
async def update_embeds():
    for guild in bot.guilds:
        player = music_players.get(guild.id)
        if not player or not player.message or not player.current:
            continue
        vc = guild.voice_client
        if not vc or not vc.is_playing():
            continue
        try:
            embed = make_now_playing_embed(player)
            await player.message.edit(embed=embed, view=MusicControlView(player))
        except:
            pass

# ============================================================
# MUSIC HELPERS
# ============================================================
yt_opts = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
}

def create_source(url: str):
    with yt_dlp.YoutubeDL(yt_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return info, info["url"]

# ============================================================
# PLAYBACK (SPOTIFY QUEUE)
# ============================================================
async def play_next(guild: discord.Guild):
    vc = guild.voice_client
    if not vc:
        return

    player = get_player(guild)

    if player.loop and player.current and player.audio_url:
        info = player.current
        audio_url = player.audio_url
    else:
        if not player.queue:
            player.current = None
            player.audio_url = None
            player.start_time = None
            player.duration = None
            return

        next_track = player.queue.pop(0)
        info = next_track["info"]
        audio_url = next_track["url"]

    player.current = info
    player.audio_url = audio_url
    player.duration = info.get("duration", 0)
    player.start_time = time.time()

    ffmpeg_opts = {
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        "options": f"-vn -filter:a volume={player.volume}"
    }
    source = discord.FFmpegPCMAudio(audio_url, executable=FFMPEG_PATH, **ffmpeg_opts)

    def after_play(err):
        asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)

    vc.play(source, after=after_play)

    if player.message:
        try:
            await player.message.edit(embed=make_now_playing_embed(player), view=MusicControlView(player))
        except:
            pass

async def start_playback(interaction: discord.Interaction, url: str):
    guild = interaction.guild
    vc = guild.voice_client
    player = get_player(guild)

    info, audio_url = create_source(url)

    if not vc.is_playing() and not player.current:
        player.current = info
        player.audio_url = audio_url
        player.duration = info.get("duration", 0)
        player.start_time = time.time()

        ffmpeg_opts = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": f"-vn -filter:a volume={player.volume}"
        }
        source = discord.FFmpegPCMAudio(audio_url, executable=FFMPEG_PATH, **ffmpeg_opts)

        def after_play(err):
            asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)

        vc.play(source, after=after_play)

        embed = make_now_playing_embed(player)
        view = MusicControlView(player)
        if interaction.response.is_done():
            player.message = await interaction.followup.send(embed=embed, view=view)
        else:
            player.message = await interaction.response.send_message(embed=embed, view=view)
    else:
        player.queue.append({"info": info, "url": audio_url})
        if interaction.response.is_done():
            await interaction.followup.send(f"➕ Added **{info.get('title', 'Unknown Title')}** to queue.")
        else:
            await interaction.response.send_message(f"➕ Added **{info.get('title', 'Unknown Title')}** to queue.")

# ============================================================
# SLASH COMMANDS
# ============================================================
@bot.tree.command(name="join")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice:
        return await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
    channel = interaction.user.voice.channel
    await channel.connect(self_deaf=True)
    await interaction.response.send_message(f"Joined **{channel.name}**.")

@bot.tree.command(name="play")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.guild.voice_client:
        if not interaction.user.voice:
            return await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
        await interaction.user.voice.channel.connect(self_deaf=True)
    await interaction.response.defer()
    if not query.startswith("http"):
        query = f"ytsearch:{query}"
    await start_playback(interaction, query)

@bot.tree.command(name="skip")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message("Nothing to skip.", ephemeral=True)
    vc.stop()
    await interaction.response.send_message("⏭ Skipped.")

@bot.tree.command(name="pause")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        vc.pause()
        player = get_player(interaction.guild)
        player.is_paused = True
        await interaction.response.send_message("⏸ Paused.")

@bot.tree.command(name="resume")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        vc.resume()
        player = get_player(interaction.guild)
        player.is_paused = False
        await interaction.response.send_message("▶️ Resumed.")

@bot.tree.command(name="loop")
async def loop(interaction: discord.Interaction):
    player = get_player(interaction.guild)
    state = player.toggle_loop()
    await interaction.response.send_message(f"🔁 Loop {'ON' if state else 'OFF'}")

@bot.tree.command(name="stop")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        vc.pause()
        player = get_player(interaction.guild)
        player.is_paused = True
        await interaction.response.send_message("⏹ Stopped (paused, queue kept).")

@bot.tree.command(name="queue")
async def queue_cmd(interaction: discord.Interaction):
    player = get_player(interaction.guild)
    if not player.queue:
        return await interaction.response.send_message("Queue is empty.", ephemeral=True)
    embed = discord.Embed(
        title="🎵 Current Queue",
        color=CYBERPUNK_COLOR
    )
    for i, track in enumerate(player.queue, start=1):
        info = track["info"]
        title = info.get("title", "Unknown Title")
        duration = info.get("duration", 0)
        if isinstance(duration, (int, float)):
            m, s = divmod(int(duration), 60)
            dur_str = f"{m}:{s:02d}"
        else:
            dur_str = "Unknown"
        embed.add_field(
            name=f"{i}. {title}",
            value=f"Duration: `{dur_str}`",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leave")
async def leave(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        return await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)
    await vc.disconnect(force=True)
    await interaction.response.send_message("👋 Left the voice channel.")

# ============================================================
# VOLUME COMMAND
# ============================================================
@bot.tree.command(name="volume")
async def volume(interaction: discord.Interaction, level: int):
    player = get_player(interaction.guild)
    if level < 0 or level > 100:
        return await interaction.response.send_message("Volume must be between 0 and 100.", ephemeral=True)
    player.volume = level / 100.0
    await interaction.response.send_message(f"🔊 Volume set to {level}%.")

# ============================================================
# ON READY
# ============================================================
@bot.event
async def on_ready():
    await bot.tree.sync()
    update_embeds.start()
    print("Bot is online.")

# ============================================================
# RUN BOT
# ============================================================
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable not set")
bot.run(TOKEN)