import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import DirectoryTree from "./DirectoryTree.jsx";

// {children: {name: node}} -- see Sidebar.jsx's buildTree, which derives
// this shape from photo.filename paths; these tests build it by hand
// instead so they don't depend on that derivation.
const TREE = {
  children: {
    vacation: {
      children: {
        2019: { children: {} },
      },
    },
    work: { children: {} },
  },
};

describe("DirectoryTree", () => {
  it("renders top-level directories only, collapsed by default", () => {
    render(<DirectoryTree node={TREE} path={null} selectedDirectory={null} onSelectDirectory={() => {}} />);

    expect(screen.getByText("vacation")).toBeInTheDocument();
    expect(screen.getByText("work")).toBeInTheDocument();
    expect(screen.queryByText("2019")).not.toBeInTheDocument();
  });

  it("expands a directory's children when its toggle is clicked, without selecting it", () => {
    const onSelectDirectory = vi.fn();
    render(<DirectoryTree node={TREE} path={null} selectedDirectory={null} onSelectDirectory={onSelectDirectory} />);

    fireEvent.click(screen.getByRole("button", { name: /expand/i }));

    expect(screen.getByText("2019")).toBeInTheDocument();
    expect(onSelectDirectory).not.toHaveBeenCalled();
  });

  it("does not render a toggle for a directory with no subdirectories", () => {
    render(<DirectoryTree node={TREE} path={null} selectedDirectory={null} onSelectDirectory={() => {}} />);

    const workRow = screen.getByText("work").closest(".directory-tree-item");
    expect(workRow.querySelector(".directory-tree-toggle")).not.toBeInTheDocument();
  });

  it("selects a directory (by its full path) when its label is clicked", () => {
    const onSelectDirectory = vi.fn();
    render(<DirectoryTree node={TREE} path={null} selectedDirectory={null} onSelectDirectory={onSelectDirectory} />);

    fireEvent.click(screen.getByRole("button", { name: /expand/i }));
    fireEvent.click(screen.getByText("2019"));

    expect(onSelectDirectory).toHaveBeenCalledWith("vacation/2019");
  });

  it("marks the currently selected directory active", () => {
    render(<DirectoryTree node={TREE} path={null} selectedDirectory="work" onSelectDirectory={() => {}} />);

    expect(screen.getByText("work").closest(".directory-tree-item")).toHaveClass("directory-tree-item-active");
    expect(screen.getByText("vacation").closest(".directory-tree-item")).not.toHaveClass(
      "directory-tree-item-active"
    );
  });
});
