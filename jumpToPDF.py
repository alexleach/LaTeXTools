import sublime, sublime_plugin, os.path, subprocess, time
import getTeXRoot

# Jump to current line in PDF file
# NOTE: must be called with {"from_keybinding": <boolean>} as arg

class jump_to_pdfCommand(sublime_plugin.TextCommand):
	def run(self, edit, **args):
		# Check prefs for PDF focus and sync
		s = sublime.load_settings("LaTeXTools Preferences.sublime-settings")
		prefs_keep_focus = s.get("keep_focus", True)
		keep_focus = self.view.settings().get("keep focus",prefs_keep_focus)
		prefs_forward_sync = s.get("forward_sync", True)
		forward_sync = self.view.settings().get("forward_sync",prefs_forward_sync)

		prefs_lin = s.get("linux")

		# If invoked from keybinding, we sync
		# Rationale: if the user invokes the jump command, s/he wants to see the result of the compilation.
		# If the PDF viewer window is already visible, s/he probably wants to sync, or s/he would have no
		# need to invoke the command. And if it is not visible, the natural way to just bring up the
		# window without syncing is by using the system's window management shortcuts.
		# As for focusing, we honor the toggles / prefs.
		from_keybinding = args["from_keybinding"]
		if from_keybinding:
			forward_sync = True
		print from_keybinding, keep_focus, forward_sync

		texFile, texExt = os.path.splitext(self.view.file_name())
		if texExt.upper() != ".TEX":
			sublime.error_message("%s is not a TeX source file: cannot jump." % (os.path.basename(view.fileName()),))
			return
		quotes = "\""
		srcfile = texFile + u'.tex'
		root = getTeXRoot.get_tex_root(self.view)
		print "!TEX root = ", repr(root) # need something better here, but this works.
		rootName, rootExt = os.path.splitext(root)
		pdffile = rootName + u'.pdf'
		(line, col) = self.view.rowcol(self.view.sel()[0].end())
		print "Jump to: ", line,col
		# column is actually ignored up to 0.94
		# HACK? It seems we get better results incrementing line
		line += 1

		# Query view settings to see if we need to keep focus or let the PDF viewer grab it
		# By default, we respect settings in Preferences

		# platform-specific code:
		plat = sublime_plugin.sys.platform
		if plat == 'darwin':
			options = ["-r","-g"] if keep_focus else ["-r"]
			if forward_sync:
				subprocess.Popen(["/Applications/Skim.app/Contents/SharedSupport/displayline"] + 
								options + [str(line), pdffile, srcfile])
			else:
				skim = os.path.join(sublime.packages_path(),
								'LaTeXTools', 'skim', 'displayfile')
				subprocess.Popen(['sh', skim] + options + [pdffile])
		elif plat == 'win32':
			# determine if Sumatra is running, launch it if not
			print "Windows, Calling Sumatra"
			# hide console
			startupinfo = subprocess.STARTUPINFO()
			startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
			tasks = subprocess.Popen(["tasklist"], stdout=subprocess.PIPE,
					startupinfo=startupinfo).communicate()[0]
			# Popen returns a byte stream, i.e. a single line. So test simply:
			if "SumatraPDF.exe" not in tasks:
				print "Sumatra not running, launch it"
				self.view.window().run_command("view_pdf")
				time.sleep(0.5) # wait 1/2 seconds so Sumatra comes up
			setfocus = 0 if keep_focus else 1
			# First send an open command forcing reload, or ForwardSearch won't 
			# reload if the file is on a network share
			command = u'[Open(\"%s\",0,%d,1)]' % (pdffile,setfocus)
			print repr(command)
			self.view.run_command("send_dde",
					{ "service": "SUMATRA", "topic": "control", "command": command})
			# Now send ForwardSearch command if needed
			if forward_sync:
				command = "[ForwardSearch(\"%s\",\"%s\",%d,%d,0,%d)]" \
							% (pdffile, srcfile, line, col, setfocus)
				print command
				self.view.run_command("send_dde",
						{ "service": "SUMATRA", "topic": "control", "command": command})

		elif 'linux' in plat: # for some reason, I get 'linux2' from sys.platform
			print("Linux!")
			launcher = LinuxPDFLauncher(line, col, srcfile, pdffile)
			launcher.launch()

		else: # ???
			pass

class LinuxPDFLauncher:
	def __init__(self, line, col, srcfile, pdffile):
		self.line = line
		self.col = col
		self.srcfile = srcfile
		self.pdffile = pdffile
		self.rootDir = os.path.dirname(srcfile)

	def launch(self):
		"""Open the generated PDF, preferably with okular, and fall back
		to evince. Currently use `which` to see if okular is installed.."""
		if subprocess.call(['which', 'okular']) == 0:
			launcher = self._launch_okular
		else:
			launcher = self._launch_evince
		from threading import Thread
		launch_thread = Thread(target=launcher)
		launch_thread.daemon = True
		launch_thread.start()
	
	def _launch(self, command, **kwds):
		print("Executing: {0}".format(' '.join(command)))
		proc = subprocess.Popen(command,
				bufsize=1,
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				**kwds)
		self._non_block_read(proc.stdout, proc.stderr)

	def _launch_okular(self):
		subcommand = 'okular --unique -p %{page} %{output}'
		#subcommand = "okular --unique {0}#src:{1}{2}".format(pdffile, line, srcfile)
		command = [ 'synctex', 'view',
					'-i', '{0}:{1}:{2}'.format(self.line, self.col, self.srcfile),
					'-o', self.pdffile,
					'-d', self.rootDir,
					'-x', subcommand ]

		self._launch(command)

	def _launch_evince(self):
		# the required scripts are in the 'evince' subdir
		ev_path = os.path.join(sublime.packages_path(), 'LaTeXTools', 'evince')
		ev_fwd_exec = os.path.join(ev_path, 'evince_forward_search')
		ev_sync_exec = os.path.join(ev_path, 'evince_sync') # for inverse search!

		# Run evince if either it's not running, or if focus PDF was toggled
		# Sadly ST2 has Python <2.7, so no check_output:
		running_apps = subprocess.Popen(['ps', 'xw'], stdout=subprocess.PIPE).communicate()[0]
		# If there are non-ascii chars in the output just captured, we will fail.
		# Thus, decode using the 'ignore' option to simply drop them---we don't need them
		running_apps = running_apps.decode(sublime_plugin.sys.getdefaultencoding(), 'ignore')

		# Run scripts through sh because the script files will lose their exec bit on github

		# Get python binary if set:
		py_binary = prefs_lin["python2"] or 'python'
		sb_binary = prefs_lin["sublime"] or 'sublime-text'
		# How long we should wait after launching sh before syncing
		sync_wait = prefs_lin["sync_wait"] or 1.0

		evince_running = ("evince " + pdffile in running_apps)
		if (not keep_focus) or (not evince_running):
			command = ['sh', ev_sync_exec, py_binary, sb_binary, pdffile],
			print "(Re)launching evince"
			self._launch(command, cwd=ev_path)
			print "launched evince_sync"
			if not evince_running: # Don't wait if we have already shown the PDF
				time.sleep(sync_wait)
		if forward_sync:
			command = [py_binary, ev_fwd_exec, pdffile, str(line), srcfile]
			self._launch(command)

	def _non_block_read(self, out, err):
		"""Run in a separate thread, to send viewer output to the
		Sublime console, not `~/.xsession-errors`
		"""
		import fcntl, select, sys
		print("start non_block_read")
		#fd = out.fileno()
		for inp in (out, err,):
			fl = fcntl.fcntl(inp, fcntl.F_GETFL)
			fcntl.fcntl(inp, fcntl.F_SETFL, fl | os.O_NONBLOCK)
		while True:
			rx = select.select([out, err], [], [])[0]
			try:
				data = ""
				for r in rx:
					data += r.read()
			except (IOError, OSError) as e:
				print("Error in non_block_read: {0}".format(e))
				time.sleep(0.5)
				continue
			if not data:
				break
			sys.stdout.write(data)
		print("non_block_read complete")

# vim: noexpandtab shiftwidth=4 sts=0 
