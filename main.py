import glassy.utils
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from json.decoder import JSONDecoder
from json.encoder import JSONEncoder
from zipfile import ZipFile
import termcolor, colorama

colorama.init()

import requests

__version__ = '1.0.0'

termcolor.cprint("hello", 'red')

running: bool = False

download_output_folder = Path('downloads')

download_output_folder.mkdir(exist_ok=True)

login: str | None = None
passw: str | None = None
steampath: Path | None = None
defaultpath: Path | None = None
settings_data = None
dst_folders: dict[int, Path] = {
}
valid_dsts: list[int]
# overwriten_appid = 294100

preload_errors = []

regex_compiler_cache: dict[str, tuple[re.Pattern, re.Pattern]] = {}
path_sep_regex = re.compile(r'[\\/]')

confirmation_overrides: dict[str, bool] = {}

_html_workshop_page_ids_extractor_re = re.compile(r"(ShowAddToCollection|SubscribeCollectionItem)[( ']+(\d+)[ ',]+(\d+)")

@cache
def html_appname_extractor_re(appid: int):
	return re.compile(rf'\s*href\s*=\s*"https://steamcommunity.com/app/{appid}"\s*>(.+?)<\s*/\s*a\s*>\s*')
@cache
def html_item_name_extractor_re():
	return re.compile(rf'\s*class\s*=\s*"workshopItemTitle"\s*>(.+?)<\s*/\s*div\s*>\s*')

@cache
def get_appname_from_html(appid: int | str, html: str):
	return html_appname_extractor_re(str(appid)).search(html)[1]
@cache
def get_item_name_from_html(html: str):
	return html_item_name_extractor_re().search(html)[1]

def get_steamcmd_content_folder():
	return steampath.joinpath("steamapps\workshop\content")

class DownloadLock:
	def __enter__(self):
		global running
		running = True
	
	def __exit__(self, *exc):
		global running
		running = False

@dataclass(slots=True, frozen=True)
class WorkshopItemInfo:
	appname: str
	name: str
	appid: int
	itemid: int

def get_credits() -> str:
	text = ["Made by BezarHere (Zaher .A Babker)"]
	return '\n'.join(text)

__folder__ = Path(__file__).parent

print(f'Starting at {__folder__}')

def to_local_path(path: str | Path):
	return __folder__.joinpath(path)


def push_text(text):
	print('  ', str(text).replace('\n', '\n  '), sep='')

def header(text: str, border_pattren: str = '###', margin: int = 6):
	border_len = len(border_pattren)
	textbox_width = len(text) + margin
	border_tiling_count = (textbox_width // border_len) + 1
	text_padding = ' ' * (((border_tiling_count * border_len) - textbox_width) // 2 + (margin // 2))
	push_text(border_pattren * border_tiling_count)
	push_text(text_padding + text + text_padding)
	push_text(border_pattren * border_tiling_count)


def request_confirmation(text: str, conf_id: str):
	if conf_id in confirmation_overrides:
		return confirmation_overrides[conf_id]
	while True:
		match input(text + '\n').strip().lower():
			case 'y' | 'yes' | 'ok' | 'true':
				return True
			case 'n' | 'no' | 'false' | 'cancel':
				return False

def modpath(base, appid, wid):
	return os.path.join(base, 'steamapps/workshop/content/', str(appid), str(wid))

def download_chunks(url: str, download_path: Path | str, chunk_size: int = 8192, recive_callback = None):
	with requests.get(url, stream=True) as r:
		r.raise_for_status()
		with open(download_path, 'wb') as f:
			for chunk in r.iter_content(chunk_size=chunk_size):
				# If you have chunk encoded response uncomment if
				# and set chunk_size parameter to None.
				# if chunk:
				f.write(chunk)
				if recive_callback:
					recive_callback(len(chunk))
				
	return download_path

def get_app_and_item_ids(url: str):
	try:
		x = requests.get(url).text
	except Exception as exc:
		push_text("Could not load workshop page for '" + url + "'\n")
		push_text(str(type(exc)) + "\n")
		push_text(str(exc) + "\n")
	else:
		if _html_workshop_page_ids_extractor_re.search(x):
			# collection
			dls = _html_workshop_page_ids_extractor_re.finditer(x)
			for i in dls:
				yield WorkshopItemInfo(get_appname_from_html(i[3], x), get_item_name_from_html(x), int(i[3]), int(i[2]))
		else:
			push_text('"' + url + '" doesn\'t look like a valid workshop item...\n')

def decoded_download_urls(urls: list[str]):
	if not urls:
		return
	pending_downloads = []
	
	for url in urls:
		if len(url) > 0:
			pending_downloads.extend(get_app_and_item_ids(url))
			
	return pending_downloads

def get_mods_folder_for_app(appid: int):
	if appid in dst_folders:
		return dst_folders[appid]
	os.mkdir(to_local_path(str(appid)))
	return to_local_path(str(appid))

def ensure_steam_cmd():
	p = steampath.joinpath('steamcmd.exe')
	if not p.exists():
		
		if steampath.exists():
			if not request_confirmation(f"No steamcmd.exe found in the steam folder '{steampath}', download the steamcmd binaries?", 'steam-nocmd'):
				quit()
		else:
			if not request_confirmation(f"steam folder '{steampath}' does not exist, download the steamcmd binaries?", 'steam-nodir'):
				quit()
			
		push_text("Downloading & Installing steamcmd ...")
		
		# get it from steam servers
		resp = download_chunks("https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip", Path("steam-cmd-download.download"))
		
		if not resp.exists():
			push_text("Failed to download the steam-cmd, please download it manuly to the steam-cmd path.")
			quit()
		with open(resp, 'rb') as f:
			ZipFile(f).extractall(steampath)
		push_text("Completed downloading the steam-cmd binaries")
	return p

def run_steamcmd(items: list[WorkshopItemInfo]):
	steamcmd_exe = ensure_steam_cmd()
	items = [i for i in items if i is not None]
	
	args = [steamcmd_exe]
	if login is not None and passw is not None:
		args.append('+login ' + login + ' ' + passw)
	else:
		args.append('+login anonymous')
	
	for i in items:
		push_text(f"requsting the workshop item '{i.name}' for '{i.appname}'")
		args.append(f'+workshop_download_item {i.appid} {int(i.itemid)}')
	args.append("+quit")
	
	# call steamcmd
	
	return subprocess.Popen(args, stdout=subprocess.PIPE, errors='ignore',
							   creationflags=subprocess.CREATE_NO_WINDOW)

def deploy_downloaded_item(i: WorkshopItemInfo):
	app_download_output = download_output_folder.joinpath(i.appname)
	app_download_output.mkdir(exist_ok=True)
	workshop_download_output = app_download_output.joinpath(i.name)
	cur = get_steamcmd_content_folder().joinpath(str(i.appid)).joinpath(str(i.itemid))
	if not cur.exists():
		return
	shutil.copytree(cur, workshop_download_output)

def deploy_all(i: list[WorkshopItemInfo | None]):
	for j in i:
		if j is None:
			continue
		deploy_downloaded_item(j)

def download(urls: list[str]):
	# don't start multiple steamcmd instances
	urls = [i.strip('"') for i in urls]
	
	if running:
		return
	
	downloads = decoded_download_urls(urls)
	
	if downloads is None:
		return
	else:
		if len(downloads) == 0:
			push_text(f'Requesting a download with no urls')
			return
		push_text(f'Started {len(downloads)} Download(s)')
	
	with DownloadLock():
		process = run_steamcmd(downloads)
		
		while True:
			out = process.stdout.readline()
			if m := re.search("Redirecting stderr to", out):
				push_text(out[:m.span()[0]] + "\n")
				break
			if re.match("-- type 'quit' to exit --", out):
				continue
			push_text(out)
			
			return_code = process.poll()
			if return_code is not None:
				# for out in process.stdout.readlines():
				# 	push_text(out)
				break
	deploy_all(downloads)


def load_settings():
	settings_path = to_local_path('settings.json')
	json_res = {}
	
	if settings_path.exists() and settings_path.is_dir():
		with open(settings_path, 'r') as f:
			text = f.read().strip()
			try:
				json_res = JSONDecoder().decode(text)
			except Exception as e:
				print(f"Faild to parse the settings json: {e}")
	
	
	if not 'theme' in json_res or not isinstance(json_res['theme'], str):
		json_res['theme'] = 'dark'
	if not 'steampath' in json_res or not isinstance(json_res['steampath'], str):
		json_res['steampath'] = Path('steamcmd')
	
	if not 'username' in json_res or not isinstance(json_res['username'], str):
		json_res['username'] = ''
	if not 'password' in json_res or not isinstance(json_res['password'], str):
		json_res['password'] = ''
	
	if not 'batchsize' in json_res or not isinstance(json_res['batchsize'], (int, float)):
		json_res['batchsize'] = 5
	
	if not 'dst_folders' in json_res or not isinstance(json_res['dst_folders'], dict):
		json_res['dst_folders'] = {}
	
	for k, v in json_res['dst_folders'].items():
		dst_folders[k] = v
		if not isinstance(k, (float, int)):
			preload_errors.append(
				AttributeError(f'the field "dst_folders" in the settings field has an invalid appid {k} '))
			continue
		if not isinstance(v, str):
			preload_errors.append(
				AttributeError(f'the field "dst_folders[{k}]" in the settings field has an invalid value "{v}" '))
			continue
		if not os.path.exists(v):
			preload_errors.append(AttributeError(
				f'the field "dst_folders[{k}]" in the settings field has a non existing folder path "{v}" '))
			continue
		if not os.path.isdir(v):
			preload_errors.append(AttributeError(
				f'the field "dst_folders[{k}]" in the settings field has a path "{v}" that doesn\' lead to a folder '))
			continue
		valid_dsts.append(k)
	
	with open(settings_path, 'w') as f2:
		f2.write(JSONEncoder(indent=4, default=lambda o: str(o)).encode(json_res))
	
	return json_res


def proc_input(line: str):
	b = list(glassy.utils.to_args(line))
	
	if not b:
		return
	
	command = b[0].strip()
	is_bare_download_command = False
	
	if command[:4] == 'http':
		if command[4:7] == 's:/' or command[4:7] == '://':
			is_bare_download_command = True
	
	if is_bare_download_command:
		proc_input('sub ' + ' '.join([f'"{i}"' for i in b]))
		return
	
	
	
	b = b[1:]
	
	match command:
		case 'download' | 'sub' | 'subscribe':
			download(b)
		case 'quit' | 'q' | 'exit':
			quit()
		case _:
			push_text(f"Unknown command: '{command}'")
		

def excute_run_arguments(args: list[str]):
	subargs = [[]]
	for i in args:
		if i == '*':
			subargs.append([])
			continue
		subargs[-1].append(f'"{i}"')
	
	for i in subargs:
		if not i:
			continue
		proc_input(' '.join(i))

def main():
	global settings_data
	global steampath
	global defaultpath
	global login
	global passw
	global running
	running = False
	
	settings_data = load_settings()
	
	# set globals
	steampath = settings_data['steampath']
	# defaultpath = json_data.get('general', 'defaultpath', fallback=None)
	login = None
	passw = None
	if len(settings_data['username']) >= 4:
		login = settings_data['username']
	if len(settings_data['password']) >= 4:
		passw = settings_data['password']
	
	# print(f'settings loaded: {settings_data}')
	
	push_text(f'Version: {__version__}')
	header(get_credits(), border_pattren='# --- #', margin=16)
	
	for i in preload_errors:
		push_text(i)
	
	ensure_steam_cmd()
	
	if len(sys.argv) >= 2 and sys.argv[1] == '/c':
		excute_run_arguments(sys.argv[2:])
	else:
		while True:
			proc_input(input('> '))
	


if __name__ == '__main__':
	main()
