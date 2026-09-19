# Games that use the physical board

These ideas build on the same planar wall mask and laser/button inputs. They are extensions, not features already implemented in Beaver Battle.

- **Draw-and-erase dams:** redirect boats by drawing channels, then erase a gap to release a trapped player. Existing live wall collision already supports the basic interaction; animated water flow would be a separate game rule.
- **Team bridge builder:** one player steers while a teammate redraws the route around hazards. Put a time or ink-length budget on construction to prevent enclosing every opponent.
- **Moving islands:** slide flat dark cards across the board as shelters. The current game relocates trapped entities to free space; a future pushing mechanic would need separate collision rules.
- **Shadow gates:** hold a hand over a lane long enough to close it, then remove it to reopen. Persistence filters ignore very brief shadows; prolonged hands intentionally count as obstacles. This is a silhouette, not height sensing.
- **Erase-to-rescue:** draw a maze before a round, then erase doors to help an ejected beaver reach its team. Team scoring and rescue rules would be new game mechanics.
- **Physical puzzle tokens:** use distinct printed ArUco tokens as switches, goalposts, or capture points. Marker detection exists for calibration; tracking additional token IDs during gameplay would require a small separate detector and game contract.

For any new game, keep projected art bright and reserve small saturated red regions for physical lasers. Real ink cannot be digitally destroyed; represent destructible objects with projected geometry and use erasure for physical terrain changes. Avoid rules that reward pointing controllers at faces; all aiming happens on the board. The chosen hit feedback is the servo-operated water mechanism, with its travel and power designed separately.
