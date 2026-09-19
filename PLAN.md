PLAN MODE

Your objective is to help us build the following system. This is for a hackathon and must be done in 4 hours.

What are we making?
Projection mapped game!

Act as an expert electrical engineer, software engineer, and game developer. Guide us through any hardware and wiring needed.

The overall hardware system involves:
Custom controllers, each with a laser pointer, two buttons, and a battery that must wirelessly communicate with a central laptop that will run the game.
The controller has an Elegoo ESP32 board for central computation and wireless communication
The laser diodes are 5V, 20mA diodes
We will have simple buttons that act as open/close switches.
A projector that serves as the screen, wired directly to the central laptop.
A camera, an ArduCam, mounted on the projector that watches the surface (which will be a whiteboard) that the projector is projecting onto, detects drawings on the surface, then routes to the central laptop.
Ideally while playing, the players will not need to touch the central laptop. It is for computing purposes only.

Software and controls system will involve:
The camera will detect the laser pointer's position on the projected image. The laptop processing the camera will need to map the position of the laser dot to an x,y coordinate in the game screen.
The camera will likely need to be calibrated with calibration markers. You will need to program this.
You also need to program some type of edge detection such that existing drawings on the surface (its a whiteboard) or objects placed on the board are registered as some game element.
To tell apart whose laser is who, our idea is to have the lasers be pulsed with PWM and to have the camera detect the pulse rate.
One way to do this is to keep the laser high for 20/30 frames and low for 5/30 frames. The distance between the low pulses identifies which laser is which.
Another idea: keep the laser low for 5 frames out of every N, and N is different per laser (20, 30, 40)
Some experimentation will be required to reliably execute this. If there exists better options, pitch them.
Software stack
Camera streams frames to laptop, some service makes this available at low latency
Some service takes in raw frames and processes it through the following steps:
Maps it using the calibrated map to flat 2D space
Detects drawn edges, locations of lasers
Makes this available in some data type

The game and its mechanics will be:
Players: Beavers! In canoes! Throwing rocks at one another (this is the destructive projectile).
Sprites: Use placeholders for now (colored polygons). We will draw our own sprites and provide them to you later.
Direction: Laser pointer. Players follow their own laser pointer at maximum speed. Within a certain small radius, the players stop following the pointer.
Rocks are thrown straight ahead and have a fixed firing rate and a max ejection before a reload downtime (make both this editable). Throwing is triggered by the controller button.
Upon getting hit, sprite becomes smaller sprite (beaver ejected from canoe) and floats around at lower speed, direction still controlled by laser, acceleration with shoot button
Other features
Destructible Arenas: Maps feature destructible walls, barriers, asteroids, and hazards like death beams or turrets, changing the battlefield as the match progresses.
Power-Ups: Shooting barrels or asteroids spawns random weapons like lasers, jousters, and proximity mines. These should have some movement in certain situations and should obey the solid walls of the drawings on the whiteboard
Other physics
The Overheat Penalty: Firing is constrained by an ammo-recharge cycle. You have exactly three shots in your primary reserve. Spamming them immediately triggers an overheat/recharge state, leaving you entirely defenseless for a few crucial seconds.
Wall Forgiveness: Ramming into boundaries or obstacles does zero structural damage to your ship. Instead, your ship cleanly bounces or slides along the geometry, turning the arena perimeter into a tool for physics-based redirection.
Continuous Inertia & Forward Momentum: Ships can never come to a full stop. When you are not actively pressing the turn button, your ship moves forward in a straight line.
References:
Astro party: https://github.com/sam-jwang/Astro-Party

You should also be ready to program other games too. The underlying control and hardware system and the game should be somewhat compartmentalized.

You will also make this into a git repo, with documentation, readme, and everything needed. You will also add enough detail for other AI agents to pull code, edit code, and push the code back. This will be a large, coordinated process across multiple agents and users. Ensure the architecture is robust enough to survive this.

Coding all of this will also require deploying a fleet of subagents.

Opinionated code style notes:
Never use “from **future** import annotations”
Never use underscores before variable names or functions
Write only ever the minimal amount of code needed to achieve a goal
Follow rules in https://github.com/DietrichGebert/ponytail
Use C for firmware and Python for game engine, projection stack

In addition, help us brainstorm other creative ways we can take advantage of the physical aspect of our system in our games.

We were also thinking of having some sort of physical feedback for if you get hurt in game. Like your controller zaps you, or it squirts water at you, or something like that. This is a very necessary feature.

Make a plan and ask questions before you begin.

Be smart.
