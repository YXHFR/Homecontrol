// Matar LD2461-komponenten med ramar och skriver ut vad sensorerna fick.
//
// Varje rad på stdin är byte i hex, till exempel en ram. Efter varje rad
// körs komponentens loop() och update(), och en rad skrivs ut: om en ny ram
// med mål har lästs (take_new_coordinates) och X Y i mm för de fem målen.
#include <iostream>
#include <sstream>

#include "ld2461.h"

using namespace esphome;

int main() {
  ld2461::LD2461 radar;
  sensor::Sensor xs[5], ys[5];
  radar.set_position_x_0_sensor(&xs[0]);
  radar.set_position_y_0_sensor(&ys[0]);
  radar.set_position_x_1_sensor(&xs[1]);
  radar.set_position_y_1_sensor(&ys[1]);
  radar.set_position_x_2_sensor(&xs[2]);
  radar.set_position_y_2_sensor(&ys[2]);
  radar.set_position_x_3_sensor(&xs[3]);
  radar.set_position_y_3_sensor(&ys[3]);
  radar.set_position_x_4_sensor(&xs[4]);
  radar.set_position_y_4_sensor(&ys[4]);

  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream hex(line);
    unsigned byte;
    while (hex >> std::hex >> byte) {
      radar.rx.push_back(static_cast<uint8_t>(byte));
    }
    radar.loop();
    bool fresh = radar.take_new_coordinates();
    radar.update();
    std::cout << fresh;
    for (int i = 0; i < 5; i++) {
      std::cout << ' ' << xs[i].state << ' ' << ys[i].state;
    }
    std::cout << '\n';
  }
}
