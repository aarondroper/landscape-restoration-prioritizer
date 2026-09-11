import type { ComponentProps } from "react";

export type IconName =
  | "app-logo.svg"
  | "component-habitat.svg"
  | "component-network.svg"
  | "component-riparian.svg"
  | "component-protected.svg"
  | "component-availability.svg"
  | "top-candidates.svg"
  | "map-layers.svg";

interface IconProps extends Omit<ComponentProps<"img">, "src" | "alt"> {
  name: IconName;
  alt?: string;
}

export function Icon({ name, alt = "", ...props }: IconProps) {
  return <img src={`/icons/${name}`} alt={alt} {...props} />;
}
