import { describe, expect, it } from "vitest";
import { parseRoomLines } from "./layout";

describe("parseRoomLines", () => {
  it("reads a name and a width x depth in the ways a broker types them", () => {
    const { rooms, bad } = parseRoomLines("Living room 5.5 x 4.2\nKitchen: 3.2x3\nMaster bedroom 4,2 × 3.6 m\n\nBath - 2.4 * 1.8");
    expect(bad).toEqual([]);
    expect(rooms).toEqual([
      { name: "Living room", w: 5.5, d: 4.2 },
      { name: "Kitchen", w: 3.2, d: 3 },
      { name: "Master bedroom", w: 4.2, d: 3.6 },
      { name: "Bath", w: 2.4, d: 1.8 },
    ]);
  });

  it("keeps a number in the room's name and returns the lines it cannot read", () => {
    const { rooms, bad } = parseRoomLines("Bedroom 2 3.6 x 3.2\nBalcony\nHall 0.2 x 3\nWarehouse 500 x 4");
    expect(rooms).toEqual([{ name: "Bedroom 2", w: 3.6, d: 3.2 }]);
    expect(bad).toEqual(["Balcony", "Hall 0.2 x 3", "Warehouse 500 x 4"]);
  });
});
