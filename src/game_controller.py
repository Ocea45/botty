import os
import threading
import time

import cv2

from utils.auto_settings import check_settings
from bot import Bot
from config import Config
from death_manager import DeathManager
from game_recovery import GameRecovery
from game_stats import GameStats
from health_manager import HealthManager
from logger import Logger
from messenger import Messenger
from screen import Screen
from utils.misc import kill_thread
from utils.restart import restart_game
from utils.misc import kill_thread, set_d2r_always_on_top, restore_d2r_window_visibility

class GameController:
    is_running = False

    def __init__(self, config: Config):
        self._config = config
        self.screen = None
        self.health_monitor_thread = None
        self.health_manager = None
        self.death_manager = None
        self.death_monitor_thread = None
        self.game_recovery = None
        self.game_stats = None
        self.game_controller_thread = None
        self.bot_thread = None
        self.bot = None

    def run_bot(self, pick_corpse: bool = False):
        # Start bot thread
        self.bot = Bot(self.screen, self.game_stats, pick_corpse)
        self.bot_thread = threading.Thread(target=self.bot.start)
        self.bot_thread.daemon = True
        self.bot_thread.start()
        # Register that thread to the death and health manager so they can stop the bot thread if needed
        self.death_manager.set_callback(lambda: self.bot.stop() or kill_thread(self.bot_thread))
        self.health_manager.set_callback(lambda: self.bot.stop() or kill_thread(self.bot_thread))
        self.health_manager.set_belt_manager(self.bot.get_belt_manager())
        do_restart = False
        messenger = Messenger()
        while 1:
            self.health_manager.update_location(self.bot.get_curr_location())
            max_game_length_reached = self.game_stats.get_current_game_length() > self._config.general["max_game_length_s"]
            if max_game_length_reached or self.death_manager.died() or self.health_manager.did_chicken():
                # Some debug and logging
                if max_game_length_reached:
                    Logger.info(f"Max game length reached. Attempting to restart {self._config.general['name']}!")
                    if self._config.general["info_screenshots"]:
                        cv2.imwrite("./info_screenshots/info_max_game_length_reached_" + time.strftime("%Y%m%d_%H%M%S") + ".png", self.screen.grab())
                elif self.death_manager.died():
                    self.game_stats.log_death()
                elif self.health_manager.did_chicken():
                    self.game_stats.log_chicken()
                self.bot.stop()
                kill_thread(self.bot_thread)
                # Try to recover from whatever situation we are and go back to hero selection
                do_restart = self.game_recovery.go_to_hero_selection()
                break
            time.sleep(0.5)
        self.bot_thread.join()
        if do_restart:
            # Reset flags before running a new bot
            self.death_manager.reset_death_flag()
            self.health_manager.reset_chicken_flag()
            self.game_stats.log_end_game(failed=max_game_length_reached)
            return self.run_bot(True)
        else:
            if self._config.general["info_screenshots"]:
                cv2.imwrite("./info_screenshots/info_could_not_recover_" + time.strftime("%Y%m%d_%H%M%S") + ".png", self.screen.grab())
            Logger.error(
                f"{self._config.general['name']} could not recover from a max game length violation. Restarting the Game.")
            if self._config.general["custom_message_hook"]:
                messenger.send(msg=f"{self._config.general['name']}: got stuck and will now restart D2R")
            if restart_game(self._config.general["d2r_path"]):
                self.game_stats.log_end_game(failed=max_game_length_reached)
                if self.setup_screen():
                    self.start_health_manager_thread()
                    self.start_death_manager_thread()
                    self.game_recovery = GameRecovery(self.screen, self.death_manager)
                    return self.run_bot(True)
            Logger.error(f"{self._config.general['name']} could not restart the game. Quitting.")
            if self._config.general["custom_message_hook"]:
                messenger.send(msg=f"{self._config.general['name']}: got stuck and will now quit")
            os._exit(1)

    def start(self):
        if self._config.advanced_options['d2r_windows_always_on_top']:
            set_d2r_always_on_top()
        self.setup_screen()
        self.start_health_manager_thread()
        self.start_death_manager_thread()
        self.game_recovery = GameRecovery(self.screen, self.death_manager)
        self.game_stats = GameStats()
        self.start_game_controller_thread()
        GameController.is_running = True

    def stop(self):
        if self._config.advanced_options['d2r_windows_always_on_top']:
            restore_d2r_window_visibility()
        if self.death_monitor_thread: kill_thread(self.death_monitor_thread)
        if self.health_monitor_thread: kill_thread(self.health_monitor_thread)
        if self.bot_thread: kill_thread(self.bot_thread)
        if self.game_controller_thread: kill_thread(self.game_controller_thread)
        GameController.is_running = False
       
    def setup_screen(self):
        # Check if we user should update the d2r settings
        diff = check_settings(self._config)
        if len(diff) > 0:
            Logger.warning("Your D2R settings differ from the requiered ones. Please use Auto Settings to adjust them. The differences are:")
            Logger.warning(f"{diff}")
        self.screen = Screen(self._config.general["monitor"])
        if self.screen.found_offsets:
            return True
        return False

    def start_health_manager_thread(self):
        # Run health monitor thread
        self.health_manager = HealthManager(self.screen)
        self.health_monitor_thread = threading.Thread(target=self.health_manager.start_monitor)
        self.health_monitor_thread.daemon = True
        self.health_monitor_thread.start()

    def start_death_manager_thread(self):
        # Run death monitor thread
        self.death_manager = DeathManager(self.screen)
        self.death_monitor_thread = threading.Thread(target=self.death_manager.start_monitor)
        self.death_monitor_thread.daemon = True
        self.death_monitor_thread.start()

    def start_game_controller_thread(self):
        # Run game controller thread
        self.game_controller_thread = threading.Thread(target=self.run_bot)
        self.game_controller_thread.daemon = False
        self.game_controller_thread.start()

    def toggle_pause_bot(self):
        if self.bot: self.bot.toggle_pause()
