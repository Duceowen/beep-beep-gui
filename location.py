import time

class Location:
    def __init__(self):
        self.state = None
        self.prev_state = None
        self.system = None
        self.prev_system = None
        self.instance = {}
        self.wing = False
        self.jump_ts = None
        self.wing_join = None
        self.interdiction_ts = None
        self.interdiction_complete = False
        self.jump_backup = {}
        self.pvp_kill_ts = None
        self.pvp_kill_victim = None
        self.departed = []
        self.on_instance_reset = None

    def _record_departures(self):
        for cmdr_id, info in self.instance.items():
            if info.get("here", False):
                self.departed.append(cmdr_id)

    def set_wing(self, in_wing: bool):
        self.wing = in_wing

    def set(self, state=None, system=None, event=None):
        if event == "SupercruiseExit" and self.interdiction_ts is not None:
            now_ts = time.time()

            if now_ts - self.interdiction_ts > 120:
                self.interdiction_ts = None

            
        if state != self.state and system != self.system:
            self._record_departures()
            self.instance = {}
            self._fire_instance_reset() 
            
        self.prev_system = self.system
        self.prev_state = self.state
        self.state = state
        self.system = system
        
    def get(self):
        return self.state, self.system

    def get_instance(self):
        return self.instance
    
    def add_instance(self, id_, state=None, system=None):
        if state is None:
            state = -1
        if system is None:
            system = "Unknown"
    
        here = (system == self.system and state == self.state)
    
        self.instance[id_] = {
            "state": state,
            "system": system,
            "here": here
        }
         
    def jump(self):
        if self.prev_system != self.system:
            self.jump_ts = time.time()
            self._record_departures()
            self.jump_backup = self.instance.copy()
            self.instance.clear()
            self._fire_instance_reset()
            
        else:
            self.jump_ts = None
            self.jump_backup = {}
            
    def wing_changed(self): 
        self.wing_join = time.time()
        
    def interdiction(self):
        self.interdiction_ts = time.time()
        
    def pvp_kill(self, victim):
        self.pvp_kill_ts = time.time()
        self.pvp_kill_victim = victim

    def _fire_instance_reset(self):
        callback = self.on_instance_reset
        if callback is not None:
            callback()
        
        
location = Location()
