// The photographs on the page: real documents and real places, all public domain. One list, so each picture is
// described the same way wherever it appears.
export const PHOTOS = {
  "receipts": { src: "/photos/receipts.jpg", width: 1024, height: 683, alt: "A loose stack of printed shop receipts" },
  "invoice-paris": { src: "/photos/invoice-paris.jpg", width: 1024, height: 670, alt: "A handwritten nineteenth-century invoice from a Paris seed merchant, ruled in columns" },
  "invoice-new-york": { src: "/photos/invoice-new-york.jpg", width: 1024, height: 833, alt: "A ruled New York invoice from 1863 with a printed header and handwritten lines" },
  "invoice-london": { src: "/photos/invoice-london.jpg", width: 1600, height: 1235, alt: "An eighteenth-century trade card for an angling supplier, with a handwritten receipt beside it" },
  "receipt-lyon": { src: "/photos/receipt-lyon.jpg", width: 1600, height: 682, alt: "A blank printed receipt form from a Lyon car dealer" },
  "ledger": { src: "/photos/ledger.jpg", width: 1600, height: 1200, alt: "An open account book with handwriting and a pencil sketch" },
  "post-room": { src: "/photos/post-room.jpg", width: 1600, height: 1082, alt: "Rows of postal clerks sorting mail by hand at long tables" },
  "dockside": { src: "/photos/dockside.jpg", width: 1600, height: 1208, alt: "Dockworkers and sailors on a ship's deck as sacks of grain are unloaded" },
  "cargo": { src: "/photos/cargo.jpg", width: 1600, height: 1323, alt: "Men positioning bales of wool in the hold of a ship" },
  "card-room": { src: "/photos/card-room.jpg", width: 1600, height: 1200, alt: "Long rows of wooden card-catalogue cabinets in a records room" },
  "card-drawer": { src: "/photos/card-drawer.jpg", width: 1600, height: 1068, alt: "A drawer of index cards, filed in order" },
  "savings-book": { src: "/photos/savings-book.jpg", width: 1920, height: 1452, alt: "Two open pages of a savings book: ruled columns, handwriting and round stamps" },
  "catalogue-drawers": { src: "/photos/catalogue-drawers.jpg", width: 1024, height: 768, alt: "Rows of small wooden drawers, each with a typed label" },
} as const;

export type PhotoName = keyof typeof PHOTOS;
