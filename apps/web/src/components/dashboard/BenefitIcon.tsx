import {
  Ambulance,
  Bandage,
  BedDouble,
  Bone,
  Brain,
  Eye,
  Footprints,
  Glasses,
  Hand,
  HandHeart,
  HeartPulse,
  Hospital,
  Leaf,
  PersonStanding,
  Pill,
  Plane,
  ShieldCheck,
  Stethoscope,
  Toothbrush,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import { createElement } from "react";

import type { Benefit, Category } from "@/domain/types";
import { cn } from "@/lib/cn";

const BY_WORD: Array<[RegExp, LucideIcon]> = [
  [/massage|rmt/i, Hand],
  [/physio/i, PersonStanding],
  [/chiro/i, Bone],
  [/naturopath/i, Leaf],
  [/psych|mental|counsel/i, Brain],
  [/eye exam|eye test|optometrist exam/i, Eye],
  [/glasses|optical|eyewear|contact/i, Glasses],
  [/drug|pharmac|prescription/i, Pill],
  [/orthotic|foot/i, Footprints],
  [/compression|stocking/i, Bandage],
  [/semi-private|hospital room|admission|hospital/i, BedDouble],
  [/dental|dentist/i, Toothbrush],
  [/travel|out-of-province|abroad/i, Plane],
  [/ambulance/i, Ambulance],
];

const BY_KIND: Record<Category["kind"], LucideIcon> = {
  paramedical: HandHeart,
  vision: Eye,
  dental: Toothbrush,
  drugs: Pill,
  hospital: Hospital,
  extras: HeartPulse,
  medical_equipment: Stethoscope,
  ambulance: Ambulance,
  travel: Plane,
  hsa: Wallet,
  other: ShieldCheck,
};

export function benefitIcon(benefit: Pick<Benefit, "name" | "keywords"> | null, kind?: Category["kind"]): LucideIcon {
  if (benefit) {
    const text = `${benefit.name} ${benefit.keywords.join(" ")}`;
    const exact = BY_WORD.find(([re]) => re.test(benefit.name));
    if (exact) return exact[1];
    const loose = BY_WORD.find(([re]) => re.test(text));
    if (loose) return loose[1];
  }
  return kind ? BY_KIND[kind] : ShieldCheck;
}

export function BenefitGlyph({
  benefit,
  kind,
  className,
}: {
  benefit: Pick<Benefit, "name" | "keywords"> | null;
  kind?: Category["kind"];
  className?: string;
}) {
  return createElement(benefitIcon(benefit, kind), {
    "aria-hidden": true,
    strokeWidth: 1.5,
    className: cn("size-6 sm:size-7", className),
  });
}
