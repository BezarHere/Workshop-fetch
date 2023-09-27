import glassy.utils
import os
import re
import subprocess
import whichcraft
from pathlib import Path
from json.decoder import JSONDecoder
from json.encoder import JSONEncoder
from zipfile import ZipFile

import requests

__version__ = '1.0.0'


running: bool = False

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

def get_credits() -> str:
	text = ["Made by BezarHere"]
	return '\n'.join(text)

__folder__ = Path(__file__).parent

print(f'Starting at {__folder__}')

def to_local_path(path: str | Path):
	return __folder__.joinpath(path)


def push_text(text):
	print('  ', str(text).replace('\n', '\n  '), sep='')

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

# faster download when mixing games
# TODO
def decoded_download_urls(urls: list[str]):
	if not urls:
		return
	pending_downloads = []
	
	for line in urls:
		if len(line) > 0:
			# check for collection
			try:
				x = requests.get(line)
			except Exception as exc:
				push_text("Could not load workshop page for " + line + "\n")
				push_text(str(type(exc)) + "\n")
				push_text(str(exc) + "\n")
			
			else:
				if re.search("SubscribeCollectionItem", x.text):
					# collection
					dls = re.findall(r"SubscribeCollectionItem[( ']+(\d+)[ ',]+(\d+)'", x.text)
					for wid, appid in dls:
						pending_downloads.append((appid, wid))
				elif re.search("ShowAddToCollection", x.text):
					# single item
					wid, appid = re.findall(r"ShowAddToCollection[( ']+(\d+)[ ',]+(\d+)'", x.text)[0]
					pending_downloads.append((appid, wid))
				else:
					push_text('"' + line + '" doesn\'t look like a valid workshop item...\n')
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

def run_steamcmd(urls):
	steamcmd_exe = ensure_steam_cmd()
	
	args = [steamcmd_exe]
	if login is not None and passw is not None:
		args.append('+login ' + login + ' ' + passw)
	else:
		args.append('+login anonymous')
	
	for appid, wid in urls:
		args.append(f'+workshop_download_item {appid} {int(wid)}')
	args.append("+quit")
	
	# call steamcmd
	
	return subprocess.Popen(args, stdout=subprocess.PIPE, errors='ignore',
							   creationflags=subprocess.CREATE_NO_WINDOW)


def download(urls: list[str]):
	# don't start multiple steamcmd instances
	global running
	global settings_data
	global steampath
	global defaultpath
	global login
	global passw
	
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
		

	running = True
	
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
		
	running = False


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
	
	b[0] = b[0].strip()
	
	match b[0]:
		case 'download' | 'sub' | 'subscribe':
			download(b[1:])
		case 'quit' | 'q' | 'exit':
			quit()
		
	

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
	push_text(get_credits())
	
	for i in preload_errors:
		push_text(i)
	
	while True:
		proc_input(input('> '))
	


if __name__ == '__main__':
	main()
